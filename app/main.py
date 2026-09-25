from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.cache import dispose_cache
from app.core.config import get_settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.metrics import CONTENT_TYPE_LATEST, render_metrics
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.core.ratelimit import dispose_rate_limiter, get_rate_limiter


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_rate_limiter()
    await dispose_cache()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Первый добавленный middleware — внешний.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Cache", "ETag", "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware)

    app.state.rate_limiter = get_rate_limiter()

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    @app.get("/metrics", include_in_schema=False, tags=["observability"])
    async def metrics_endpoint() -> Response:
        return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
