"""Prometheus-метрики приложения.

Модуль импортируется один раз на процесс, поэтому все коллекторы
регистрируются в default registry ровно один раз (иначе
``Duplicated timeseries`` при повторном ``create_app()``).
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Количество HTTP-запросов",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Латентность HTTP-запросов, секунды",
    ["method", "path"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

CACHE_HITS = Counter(
    "cache_hits_total",
    "Попадания в кэш",
    ["cache"],
)

CACHE_MISSES = Counter(
    "cache_misses_total",
    "Промахи кэша",
    ["cache"],
)

CACHE_HIT_RATIO = Gauge(
    "cache_hit_ratio",
    "Доля попаданий в кэш, 0..1",
    ["cache"],
)

RATE_LIMITED = Counter(
    "rate_limited_total",
    "Запросов, отброшенных rate limiter'ом",
    ["path"],
)


def record_cache_hit(cache: str) -> None:
    CACHE_HITS.labels(cache=cache).inc()


def record_cache_miss(cache: str) -> None:
    CACHE_MISSES.labels(cache=cache).inc()


def record_request(method: str, path: str, status: int, duration: float) -> None:
    REQUESTS_TOTAL.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(duration)


def _counter_value(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return value if value is not None else 0.0


def cache_hit_ratio(cache: str = "products") -> float:
    hits = _counter_value("cache_hits_total", {"cache": cache})
    misses = _counter_value("cache_misses_total", {"cache": cache})
    total = hits + misses
    return hits / total if total else 0.0


def render_metrics() -> bytes:
    """Обновляет производные метрики и отдаёт полный текст /metrics."""
    for cache in ("products",):
        CACHE_HIT_RATIO.labels(cache=cache).set(cache_hit_ratio(cache))
    return generate_latest(REGISTRY)


__all__ = [
    "CACHE_HITS",
    "CACHE_HIT_RATIO",
    "CACHE_MISSES",
    "CONTENT_TYPE_LATEST",
    "RATE_LIMITED",
    "REQUEST_LATENCY",
    "REQUESTS_TOTAL",
    "cache_hit_ratio",
    "record_cache_hit",
    "record_cache_miss",
    "record_request",
    "render_metrics",
]
