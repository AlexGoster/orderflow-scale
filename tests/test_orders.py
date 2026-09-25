from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from tests.conftest import QueryCounter

ProductFactory = Callable[..., Awaitable[dict[str, Any]]]


async def test_create_order_computes_total(
    client: AsyncClient, auth_headers: dict[str, str], product_factory: ProductFactory
) -> None:
    first = await product_factory(price=10.5, stock=5)
    second = await product_factory(price=2.0, stock=5)

    resp = await client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"product_id": first["id"], "quantity": 2},
                {"product_id": second["id"], "quantity": 1},
            ]
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 23.0
    assert len(body["items"]) == 2
    assert body["status"] == "pending"


async def test_create_order_requires_auth(
    client: AsyncClient, product_factory: ProductFactory
) -> None:
    product = await product_factory()
    resp = await client.post(
        "/api/v1/orders", json={"items": [{"product_id": product["id"], "quantity": 1}]}
    )
    assert resp.status_code == 401


async def test_create_order_with_unknown_product_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": 999999, "quantity": 1}]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_create_order_rejects_insufficient_stock(
    client: AsyncClient, auth_headers: dict[str, str], product_factory: ProductFactory
) -> None:
    product = await product_factory(stock=2)
    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": product["id"], "quantity": 3}]},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "insufficient_stock"


async def test_duplicate_positions_are_aggregated_in_one_batch(
    client: AsyncClient, auth_headers: dict[str, str], product_factory: ProductFactory
) -> None:
    product = await product_factory(stock=100, price=4.0)
    resp = await client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"product_id": product["id"], "quantity": 2},
                {"product_id": product["id"], "quantity": 3},
            ]
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["quantity"] == 5
    assert resp.json()["total"] == 20.0

    stock = await client.get(f"/api/v1/products/{product['id']}")
    assert stock.json()["stock"] == 95


async def test_order_create_does_one_batched_product_select(
    client: AsyncClient,
    auth_headers: dict[str, str],
    product_factory: ProductFactory,
    query_counter: QueryCounter,
) -> None:
    products = [await product_factory(stock=10) for _ in range(5)]
    query_counter.reset()

    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": p["id"], "quantity": 1} for p in products]},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert len(resp.json()["items"]) == 5

    selects = [
        stmt
        for stmt in query_counter.statements
        if stmt.lstrip().upper().startswith("SELECT")
        and "FROM products" in stmt
        and "order_items" not in stmt
    ]
    assert len(selects) == 1, query_counter.statements


async def test_list_orders_query_count_is_independent_of_order_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
    product_factory: ProductFactory,
    query_counter: QueryCounter,
) -> None:
    product = await product_factory(stock=1000)
    item = {"items": [{"product_id": product["id"], "quantity": 1}]}

    await client.post("/api/v1/orders", json=item, headers=auth_headers)
    query_counter.reset()
    single = await client.get("/api/v1/orders", headers=auth_headers)
    assert single.status_code == 200
    assert len(single.json()) == 1
    queries_with_one_order = query_counter.count

    for _ in range(4):
        await client.post("/api/v1/orders", json=item, headers=auth_headers)

    query_counter.reset()
    many = await client.get("/api/v1/orders", headers=auth_headers)
    assert len(many.json()) == 5
    queries_with_five_orders = query_counter.count

    assert queries_with_five_orders == queries_with_one_order, (
        f"N+1: {queries_with_one_order} -> {queries_with_five_orders}"
    )


async def test_get_order_loads_items_in_one_query(
    client: AsyncClient,
    auth_headers: dict[str, str],
    product_factory: ProductFactory,
    query_counter: QueryCounter,
) -> None:
    first = await product_factory(stock=10)
    second = await product_factory(stock=10)
    created = await client.post(
        "/api/v1/orders",
        json={
            "items": [
                {"product_id": first["id"], "quantity": 1},
                {"product_id": second["id"], "quantity": 2},
            ]
        },
        headers=auth_headers,
    )
    order_id = created.json()["id"]

    query_counter.reset()
    resp = await client.get(f"/api/v1/orders/{order_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2
    # SELECT users (auth) + один SELECT orders LEFT OUTER JOIN order_items
    assert query_counter.count <= 3, query_counter.statements


async def test_get_order_of_another_user_returns_404(
    client: AsyncClient, auth_headers: dict[str, str], product_factory: ProductFactory
) -> None:
    product = await product_factory(stock=10)
    created = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": product["id"], "quantity": 1}]},
        headers=auth_headers,
    )
    order_id = created.json()["id"]

    await client.post(
        "/api/v1/auth/register",
        json={"email": "other@example.com", "password": "secret-pass-2", "full_name": "Other"},
    )
    other_token = (
        await client.post(
            "/api/v1/auth/login", json={"email": "other@example.com", "password": "secret-pass-2"}
        )
    ).json()["access_token"]

    resp = await client.get(
        f"/api/v1/orders/{order_id}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 404


async def test_change_order_status(
    client: AsyncClient, auth_headers: dict[str, str], product_factory: ProductFactory
) -> None:
    product = await product_factory(stock=10)
    created = await client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": product["id"], "quantity": 1}]},
        headers=auth_headers,
    )
    order_id = created.json()["id"]

    resp = await client.patch(
        f"/api/v1/orders/{order_id}/status", params={"new_status": "shipped"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "shipped"
    assert len(resp.json()["items"]) == 1
