from collections.abc import Awaitable, Callable
from typing import Any

import fakeredis
from httpx import AsyncClient

from app.core.cache import InMemoryCacheBackend, ProductCache, RedisCacheBackend
from app.core.config import get_settings
from tests.conftest import QueryCounter

ProductFactory = Callable[..., Awaitable[dict[str, Any]]]


async def test_product_list_is_miss_then_hit(
    client: AsyncClient, seeded_catalog: list[Any]
) -> None:
    first = await client.get("/api/v1/products")
    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"

    second = await client.get("/api/v1/products")
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()


async def test_product_detail_is_miss_then_hit(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    product_id = seeded_catalog[0]["id"]

    first = await client.get(f"/api/v1/products/{product_id}")
    assert first.headers["X-Cache"] == "MISS"

    second = await client.get(f"/api/v1/products/{product_id}")
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()


async def test_cache_hit_does_not_touch_database(
    client: AsyncClient, seeded_catalog: list[Any], query_counter: QueryCounter
) -> None:
    miss = await client.get("/api/v1/products")
    assert miss.headers["X-Cache"] == "MISS"

    query_counter.reset()
    hit = await client.get("/api/v1/products")
    assert hit.headers["X-Cache"] == "HIT"
    assert query_counter.count == 0, query_counter.statements


async def test_cache_is_invalidated_on_product_create(
    client: AsyncClient, seeded_catalog: list[Any]
) -> None:
    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "MISS"
    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "HIT"

    created = await client.post(
        "/api/v1/products", json={"sku": "SKU-NEW", "name": "Fresh", "price": 1.0, "stock": 1}
    )
    assert created.status_code == 201

    after = await client.get("/api/v1/products")
    assert after.headers["X-Cache"] == "MISS"
    assert len(after.json()) == len(seeded_catalog) + 1


async def test_cache_is_invalidated_on_product_update(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    product_id = seeded_catalog[0]["id"]
    assert (await client.get(f"/api/v1/products/{product_id}")).headers["X-Cache"] == "MISS"
    assert (await client.get(f"/api/v1/products/{product_id}")).headers["X-Cache"] == "HIT"

    updated = await client.patch(f"/api/v1/products/{product_id}", json={"price": 77.0})
    assert updated.status_code == 200

    after = await client.get(f"/api/v1/products/{product_id}")
    assert after.headers["X-Cache"] == "MISS"
    assert after.json()["price"] == 77.0


async def test_cache_is_invalidated_on_order_create(
    client: AsyncClient,
    auth_headers: dict[str, str],
    product_factory: ProductFactory,
) -> None:
    product = await product_factory(stock=10)
    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "MISS"
    assert (await client.get("/api/v1/products")).headers["X-Cache"] == "HIT"

    order = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": product["id"], "quantity": 3}]},
        headers=auth_headers,
    )
    assert order.status_code == 201

    after = await client.get("/api/v1/products")
    assert after.headers["X-Cache"] == "MISS"
    assert after.json()[0]["stock"] == 7


async def test_cache_keys_are_versioned(
    client: AsyncClient, seeded_catalog: list[Any], cache_backend: InMemoryCacheBackend
) -> None:
    await client.get("/api/v1/products")
    settings = get_settings()
    prefix = f"{settings.cache_key_prefix}:products"
    keys = cache_backend.stored_keys
    assert keys, "кэш должен был записать ключ"
    assert all(key.startswith(prefix) for key in keys), keys


async def test_separate_pages_do_not_share_cache_key(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    books = await client.get("/api/v1/products", params={"category": "books"})
    toys = await client.get("/api/v1/products", params={"category": "toys"})
    assert books.headers["X-Cache"] == "MISS"
    assert toys.headers["X-Cache"] == "MISS"

    assert (await client.get("/api/v1/products", params={"category": "books"})).headers[
        "X-Cache"
    ] == "HIT"
    assert (await client.get("/api/v1/products", params={"category": "toys"})).headers[
        "X-Cache"
    ] == "HIT"


async def test_in_memory_backend_expires_after_ttl() -> None:
    now = [0.0]
    backend = InMemoryCacheBackend(clock=lambda: now[0])
    await backend.set("key", "value", ttl=10)

    assert await backend.get("key") == "value"
    now[0] = 9.0
    assert await backend.get("key") == "value"
    now[0] = 11.0
    assert await backend.get("key") is None


async def test_in_memory_backend_delete_prefix_only_removes_own_keys() -> None:
    backend = InMemoryCacheBackend()
    await backend.set("v1:products:list:all:0:50", "[]")
    await backend.set("v1:products:item:1", "{}")
    await backend.set("v1:orders:list", "[]")

    assert await backend.delete_prefix("v1:products") == 2
    assert await backend.get("v1:orders:list") == "[]"
    assert await backend.delete("missing-key") == 0


async def test_product_cache_invalidation_is_namespace_scoped() -> None:
    backend = InMemoryCacheBackend()
    cache = ProductCache(backend, prefix="v1:demo", ttl=60)
    await backend.set(cache.list_key(offset=0, limit=50), "[]")
    await backend.set(cache.item_key(1), "{}")
    await backend.set("v1:demo:orders", "[]")

    assert await cache.invalidate() == 2
    assert await backend.get("v1:demo:orders") == "[]"


async def test_product_cache_records_hit_and_miss_keys() -> None:
    backend = InMemoryCacheBackend()
    cache = ProductCache(backend, prefix="v1:demo", ttl=60)
    key = cache.list_key(offset=0, limit=20, category="books")

    assert await cache.get(key) is None
    await cache.set(key, "[]")
    assert await cache.get(key) == "[]"
    assert key in cache.backend.stored_keys  # type: ignore[attr-defined]


async def test_redis_cache_backend_roundtrip() -> None:
    fake = fakeredis.FakeAsyncRedis(decode_responses=True)
    backend = RedisCacheBackend("redis://localhost:6379/0", client=fake)

    assert await backend.get("k") is None
    await backend.set("k", "v", ttl=60)
    assert await backend.get("k") == "v"
    assert await backend.ping() is True

    await backend.set("v1:products:item:1", "{}")
    assert await backend.delete("k") == 1
    assert await backend.delete_prefix("v1:products") == 1
    assert await backend.get("v1:products:item:1") is None

    await backend.close()
