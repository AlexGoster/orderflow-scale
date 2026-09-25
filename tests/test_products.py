from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

ProductFactory = Callable[..., Awaitable[dict[str, Any]]]


async def test_create_product(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/products",
        json={
            "sku": "SKU-0001",
            "name": "Widget",
            "category": "tools",
            "price": 9.99,
            "stock": 5,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["sku"] == "SKU-0001"
    assert body["category"] == "tools"
    assert body["is_active"] is True


async def test_create_product_duplicate_sku_returns_409(
    client: AsyncClient, product_factory: ProductFactory
) -> None:
    await product_factory(sku="SKU-DUP")
    resp = await client.post(
        "/api/v1/products",
        json={"sku": "SKU-DUP", "name": "Other", "price": 1.0, "stock": 1},
    )
    assert resp.status_code == 409


async def test_list_products_seeded(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    resp = await client.get("/api/v1/products")
    assert resp.status_code == 200
    assert len(resp.json()) == len(seeded_catalog)


async def test_list_products_filters_by_category(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    resp = await client.get("/api/v1/products", params={"category": "books"})
    assert resp.status_code == 200
    assert {item["category"] for item in resp.json()} == {"books"}
    assert len(resp.json()) == 2


async def test_get_product_by_id(client: AsyncClient, seeded_catalog: list[dict[str, Any]]) -> None:
    product_id = seeded_catalog[0]["id"]
    resp = await client.get(f"/api/v1/products/{product_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == product_id


async def test_get_product_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/products/424242")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_update_product(client: AsyncClient, seeded_catalog: list[dict[str, Any]]) -> None:
    product_id = seeded_catalog[0]["id"]
    resp = await client.patch(
        f"/api/v1/products/{product_id}", json={"price": 42.5, "name": "Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["price"] == 42.5
    assert resp.json()["name"] == "Renamed"


async def test_update_product_not_found(client: AsyncClient) -> None:
    resp = await client.patch("/api/v1/products/999", json={"price": 1.0})
    assert resp.status_code == 404


async def test_list_products_pagination(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    resp = await client.get("/api/v1/products", params={"offset": 1, "limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == seeded_catalog[1]["id"]
