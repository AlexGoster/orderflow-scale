"""HTTP-middleware: request_id + JSON-лог + метрики + rate limiting."""

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import RATE_LIMITED, record_request
from app.core.ratelimit import SlidingWindowRateLimiter

REQUEST_ID_HEADER = "X-Request-ID"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NUMERIC_SEGMENT_RE = re.compile(r"/\d+")

logger = get_logger("orderflow.http")


def normalize_path(path: str) -> str:
    """Гасит cardinality метрик: /api/v1/products/42 -> /api/v1/products/{id}."""
    return _NUMERIC_SEGMENT_RE.sub("/{id}", path) or "/"


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _request_id(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Проставляет X-Request-ID, пишет структурный лог и метрики запроса."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        path = normalize_path(request.url.path)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            record_request(request.method, path, 500, duration)
            logger.exception(
                "request_failed",
                extra={
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status": 500,
                    "duration_ms": round(duration * 1000, 3),
                },
            )
            raise

        duration = time.perf_counter() - started
        response.headers[REQUEST_ID_HEADER] = request_id
        record_request(request.method, path, response.status_code, duration)
        logger.info(
            "request_completed",
            extra={
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 3),
                "client": client_key(request),
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Скользящее окно на Redis (или in-memory в тестах), ответ 429 + Retry-After."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if request.method != "GET" or request.url.path not in settings.rate_limit_paths:
            return await call_next(request)

        limiter: SlidingWindowRateLimiter = request.app.state.rate_limiter
        decision = await limiter.check(f"rl:{client_key(request)}:{request.url.path}")
        path = normalize_path(request.url.path)

        if not decision.allowed:
            RATE_LIMITED.labels(path=path).inc()
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "event": "rate_limit",
                    "request_id": getattr(request.state, "request_id", None),
                    "path": path,
                    "client": client_key(request),
                    "retry_after": decision.retry_after,
                },
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests, slow down",
                    }
                },
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
