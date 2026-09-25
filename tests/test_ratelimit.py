from typing import Any

import fakeredis
from httpx import AsyncClient

from app.core.ratelimit import (
    InMemoryRateLimitStore,
    RedisRateLimitStore,
    SlidingWindowRateLimiter,
)
from app.main import app


def _tight_limiter(limit: int = 3, window: int = 60) -> SlidingWindowRateLimiter:
    app.state.rate_limiter = SlidingWindowRateLimiter(
        InMemoryRateLimitStore(), limit=limit, window=window
    )
    return app.state.rate_limiter


async def test_rate_limit_returns_429_with_retry_after(client: AsyncClient) -> None:
    _tight_limiter(limit=3, window=60)
    headers = {"X-Forwarded-For": "203.0.113.7"}

    for _ in range(3):
        resp = await client.get("/api/v1/products", headers=headers)
        assert resp.status_code == 200

    blocked = await client.get("/api/v1/products", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.json()["error"]["code"] == "rate_limited"


async def test_rate_limit_is_scoped_per_client(client: AsyncClient) -> None:
    _tight_limiter(limit=2, window=60)
    blocked_client = {"X-Forwarded-For": "198.51.100.10"}

    assert (await client.get("/api/v1/products", headers=blocked_client)).status_code == 200
    assert (await client.get("/api/v1/products", headers=blocked_client)).status_code == 200
    assert (await client.get("/api/v1/products", headers=blocked_client)).status_code == 429

    other = await client.get("/api/v1/products", headers={"X-Forwarded-For": "198.51.100.11"})
    assert other.status_code == 200
    assert other.headers["X-RateLimit-Limit"] == "2"
    assert other.headers["X-RateLimit-Remaining"] == "1"


async def test_rate_limit_does_not_touch_unlisted_paths(client: AsyncClient) -> None:
    _tight_limiter(limit=1, window=60)
    headers = {"X-Forwarded-For": "192.0.2.55"}

    for _ in range(3):
        assert (await client.get("/health", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/auth/register", headers=headers)).status_code == 422


async def test_rate_limit_allows_window_to_slide_forward() -> None:
    now = [1000.0]
    store = InMemoryRateLimitStore()
    limiter = SlidingWindowRateLimiter(store, limit=2, window=10, clock=lambda: now[0])

    assert (await limiter.check("ip:1")).allowed is True
    assert (await limiter.check("ip:1")).allowed is True
    third = await limiter.check("ip:1")
    assert third.allowed is False
    assert third.retry_after == 10

    now[0] = 1011.0
    assert (await limiter.check("ip:1")).allowed is True
    assert (await limiter.check("ip:1")).allowed is True
    assert (await limiter.check("ip:1")).allowed is False


async def test_in_memory_store_drops_expired_hits() -> None:
    store = InMemoryRateLimitStore()
    result = await store.record("k", window=5, now=100.0)
    assert result.count == 1
    assert result.reset_after == 5.0

    result = await store.record("k", window=5, now=103.0)
    assert result.count == 2
    assert result.reset_after == 2.0

    result = await store.record("k", window=5, now=120.0)
    assert result.count == 1
    assert result.reset_after == 5.0


async def test_redis_rate_limit_store_uses_sliding_window() -> None:
    fake: Any = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = RedisRateLimitStore(fake)

    first = await store.record("rl:1.2.3.4:/api/v1/products", window=60, now=1000.0)
    second = await store.record("rl:1.2.3.4:/api/v1/products", window=60, now=1005.0)
    assert first.count == 1
    assert second.count == 2
    assert second.reset_after == 55.0

    stale = await store.record("rl:1.2.3.4:/api/v1/products", window=60, now=1100.0)
    assert stale.count == 1

    await store.close()


async def test_rate_limiter_decision_shapes_headers(client: AsyncClient) -> None:
    _tight_limiter(limit=5, window=60)
    resp = await client.get("/api/v1/products", headers={"X-Forwarded-For": "192.0.2.77"})
    assert resp.headers["X-RateLimit-Limit"] == "5"
    assert resp.headers["X-RateLimit-Remaining"] == "4"
