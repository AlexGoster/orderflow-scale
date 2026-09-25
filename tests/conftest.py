from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.cache import InMemoryCacheBackend, get_cache
from app.core.database import Base, get_session
from app.core.ratelimit import (
    InMemoryRateLimitStore,
    SlidingWindowRateLimiter,
    get_rate_limiter,
)
from app.main import app


class QueryCounter:
    """Собирает SQL-запросы, чтобы тесты могли ловить N+1."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def reset(self) -> None:
        self.statements.clear()

    def _before_cursor_execute(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: Any,
    ) -> None:
        self.statements.append(statement)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory


@pytest.fixture
async def query_counter(engine: AsyncEngine) -> AsyncIterator[QueryCounter]:
    counter = QueryCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter._before_cursor_execute)
    yield counter
    event.remove(engine.sync_engine, "before_cursor_execute", counter._before_cursor_execute)


@pytest.fixture
async def cache_backend() -> AsyncIterator[InMemoryCacheBackend]:
    backend = InMemoryCacheBackend()
    app.dependency_overrides[get_cache] = lambda: backend
    yield backend
    app.dependency_overrides.pop(get_cache, None)


@pytest.fixture
async def rate_limiter() -> AsyncIterator[SlidingWindowRateLimiter]:
    limiter = SlidingWindowRateLimiter(InMemoryRateLimitStore(), limit=10_000, window=60)
    app.state.rate_limiter = limiter
    yield limiter
    app.state.rate_limiter = get_rate_limiter()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    cache_backend: InMemoryCacheBackend,
    rate_limiter: SlidingWindowRateLimiter,
) -> AsyncIterator[AsyncClient]:
    async def override_get_session() -> Any:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def registered_user(client: AsyncClient) -> dict[str, str]:
    email = "user@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secret-pass-1", "full_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "secret-pass-1"}
    )
    assert resp.status_code == 200, resp.text
    return {"email": email, "token": resp.json()["access_token"]}


@pytest.fixture
async def auth_headers(registered_user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registered_user['token']}"}


@pytest.fixture
async def product_factory(client: AsyncClient) -> Callable[..., Awaitable[dict[str, Any]]]:
    counter = {"n": 0}

    async def factory(**overrides: Any) -> dict[str, Any]:
        counter["n"] += 1
        payload: dict[str, Any] = {
            "sku": f"SKU-{counter['n']:04d}",
            "name": f"Product {counter['n']:04d}",
            "category": "general",
            "price": 10.0,
            "stock": 100,
            "is_active": True,
        }
        payload.update(overrides)
        resp = await client.post("/api/v1/products", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return factory


@pytest.fixture
async def seeded_catalog(
    product_factory: Callable[..., Awaitable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        await product_factory(category="books"),
        await product_factory(category="books"),
        await product_factory(category="toys"),
    ]
