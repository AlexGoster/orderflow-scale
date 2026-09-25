"""Rate limiting на скользящем окне.

Реализации хранилища:

* :class:`InMemoryRateLimitStore` — dev/тесты (Redis-сервер недоступен);
* :class:`RedisRateLimitStore` — прод: ``ZSET`` + ``ZREMRANGEBYSCORE`` в одном
  pipeline, атомарно и без гонок между воркерами.
"""

import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class WindowResult:
    count: int
    oldest: float | None
    reset_after: float


class RateLimitStore(Protocol):
    async def record(self, key: str, window: int, now: float) -> WindowResult: ...


class InMemoryRateLimitStore:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}

    async def record(self, key: str, window: int, now: float) -> WindowResult:
        bucket = self._buckets.setdefault(key, deque())
        horizon = now - window
        while bucket and bucket[0] <= horizon:
            bucket.popleft()
        bucket.append(now)
        oldest = bucket[0]
        return WindowResult(
            count=len(bucket),
            oldest=oldest,
            reset_after=max(0.0, oldest + window - now),
        )

    @property
    def buckets(self) -> dict[str, deque[float]]:
        return self._buckets


class RedisRateLimitStore:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def record(self, key: str, window: int, now: float) -> WindowResult:
        member = f"{now}:{uuid.uuid4().hex}"
        pipeline = self._client.pipeline(transaction=True)
        pipeline.zremrangebyscore(key, "-inf", now - window)
        pipeline.zadd(key, {member: now})
        pipeline.zcard(key)
        pipeline.zrange(key, 0, 0, withscores=True)
        pipeline.expire(key, window)
        results = await pipeline.execute()

        count = int(results[2])
        rows = results[3]
        oldest = float(rows[0][1]) if rows else None
        reset_after = 0.0 if oldest is None else max(0.0, oldest + window - now)
        return WindowResult(count=count, oldest=oldest, reset_after=reset_after)

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    def __init__(
        self,
        store: RateLimitStore,
        *,
        limit: int,
        window: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._limit = limit
        self._window = window
        self._clock = clock

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window(self) -> int:
        return self._window

    @property
    def store(self) -> RateLimitStore:
        return self._store

    async def check(self, key: str) -> RateLimitDecision:
        result = await self._store.record(key, self._window, self._clock())
        allowed = result.count <= self._limit
        retry_after = 0 if allowed else max(1, math.ceil(result.reset_after))
        return RateLimitDecision(
            allowed=allowed,
            limit=self._limit,
            remaining=max(0, self._limit - result.count),
            retry_after=retry_after,
        )


def build_rate_limiter(settings: Settings | None = None) -> SlidingWindowRateLimiter:
    cfg = settings or get_settings()
    if cfg.rate_limit_backend == "redis":
        store: RateLimitStore = RedisRateLimitStore(
            Redis.from_url(cfg.redis_url, decode_responses=True)
        )
    else:
        store = InMemoryRateLimitStore()
    return SlidingWindowRateLimiter(
        store,
        limit=cfg.rate_limit_requests,
        window=cfg.rate_limit_window_seconds,
    )


_rate_limiter: SlidingWindowRateLimiter | None = None


def get_rate_limiter() -> SlidingWindowRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = build_rate_limiter()
    return _rate_limiter


async def dispose_rate_limiter() -> None:
    global _rate_limiter
    if _rate_limiter is not None:
        closer = getattr(_rate_limiter.store, "close", None)
        if closer is not None:
            await closer()
        _rate_limiter = None
