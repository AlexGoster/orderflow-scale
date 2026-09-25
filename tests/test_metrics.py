from typing import Any

from httpx import AsyncClient
from prometheus_client import REGISTRY

from tests.conftest import QueryCounter

EXPECTED_METRIC_NAMES = (
    "http_requests_total",
    "http_request_duration_seconds_bucket",
    "http_request_duration_seconds_count",
    "cache_hits_total",
    "cache_misses_total",
    "cache_hit_ratio",
    "rate_limited_total",
)


def _sample(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return value if value is not None else 0.0


async def test_metrics_endpoint_exposes_expected_names(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")

    for name in EXPECTED_METRIC_NAMES:
        assert name in resp.text, f"метрика {name} отсутствует в /metrics"


async def test_metrics_counts_requests_per_route(client: AsyncClient) -> None:
    labels = {
        "method": "GET",
        "path": "/api/v1/products",
        "status": "200",
    }
    before = _sample("http_requests_total", labels)

    resp = await client.get("/api/v1/products")
    assert resp.status_code == 200

    after = _sample("http_requests_total", labels)
    assert after == before + 1


async def test_metrics_normalizes_path_cardinality(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    product_id = seeded_catalog[0]["id"]
    await client.get(f"/api/v1/products/{product_id}")

    assert (
        _sample(
            "http_requests_total",
            {"method": "GET", "path": "/api/v1/products/{id}", "status": "200"},
        )
        >= 1
    )


async def test_cache_hit_ratio_is_updated_by_catalog_reads(
    client: AsyncClient, seeded_catalog: list[object]
) -> None:
    hits_label = {"cache": "products"}
    hits_before = _sample("cache_hits_total", hits_label)
    misses_before = _sample("cache_misses_total", hits_label)

    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "MISS"
    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "HIT"

    assert _sample("cache_hits_total", hits_label) == hits_before + 1
    assert _sample("cache_misses_total", hits_label) == misses_before + 1

    body = (await client.get("/metrics")).text
    ratio_line = next(line for line in body.splitlines() if line.startswith("cache_hit_ratio"))
    ratio = float(ratio_line.rsplit(" ", 1)[1])
    assert 0.0 <= ratio <= 1.0


async def test_latency_histogram_records_observations(client: AsyncClient) -> None:
    labels = {"method": "GET", "path": "/health"}
    before = _sample("http_request_duration_seconds_count", labels)

    assert (await client.get("/health")).status_code == 200

    assert _sample("http_request_duration_seconds_count", labels) == before + 1


async def test_rate_limited_counter_increments(client: AsyncClient) -> None:
    from app.core.ratelimit import InMemoryRateLimitStore, SlidingWindowRateLimiter
    from app.main import app

    app.state.rate_limiter = SlidingWindowRateLimiter(InMemoryRateLimitStore(), limit=1, window=60)
    headers = {"X-Forwarded-For": "203.0.113.200"}
    labels = {"path": "/api/v1/products"}

    before = _sample("rate_limited_total", labels)

    assert (await client.get("/api/v1/products", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/products", headers=headers)).status_code == 429

    assert _sample("rate_limited_total", labels) == before + 1


async def test_metrics_do_not_create_sql_queries(
    client: AsyncClient, query_counter: QueryCounter
) -> None:
    query_counter.reset()
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert query_counter.count == 0
