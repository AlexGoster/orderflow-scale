import pytest

from app.core.security import create_access_token


@pytest.mark.parametrize("subject", ["abc", "0", "99999999999999999999999"])
async def test_invalid_token_subject_is_401(client, subject):
    response = await client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {create_access_token(subject)}",
        },
    )
    assert response.status_code == 401


async def test_failed_order_rolls_back_stock(client, auth_headers):
    product = await client.post(
        "/api/v1/products",
        json={
            "sku": "rollback",
            "name": "Test",
            "price": 10,
            "stock": 2,
        },
    )
    product_id = product.json()["id"]
    response = await client.post(
        "/api/v1/orders",
        headers=auth_headers,
        json={
            "items": [
                {"product_id": product_id, "quantity": 1},
                {"product_id": 999999, "quantity": 1},
            ]
        },
    )
    assert response.status_code == 404
    assert (await client.get(f"/api/v1/products/{product_id}")).json()["stock"] == 2
    assert (await client.get("/api/v1/orders", headers=auth_headers)).json() == []


async def test_status_cannot_modify_foreign_order(client, auth_headers):
    product = await client.post(
        "/api/v1/products",
        json={
            "sku": "owner",
            "name": "Test",
            "price": 10,
            "stock": 2,
        },
    )
    order = await client.post(
        "/api/v1/orders",
        headers=auth_headers,
        json={
            "items": [
                {"product_id": product.json()["id"], "quantity": 1},
            ]
        },
    )
    await client.post(
        "/api/v1/auth/register", json={"email": "foreign@example.com", "password": "test-password"}
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "foreign@example.com", "password": "test-password"}
    )
    response = await client.patch(
        f"/api/v1/orders/{order.json()['id']}/status",
        params={"new_status": "paid"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 404
