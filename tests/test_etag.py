from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

ProductFactory = Callable[..., Awaitable[dict[str, Any]]]


async def test_list_products_returns_etag(client: AsyncClient, seeded_catalog: list[Any]) -> None:
    resp = await client.get("/api/v1/products")
    assert resp.status_code == 200
    assert resp.headers["ETag"].startswith('"')
    assert len(resp.headers["ETag"]) > 10


async def test_if_none_match_returns_304_without_body(
    client: AsyncClient, seeded_catalog: list[Any]
) -> None:
    first = await client.get("/api/v1/products")
    etag = first.headers["ETag"]

    second = await client.get("/api/v1/products", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == etag
    assert second.headers["X-Cache"] == "HIT"


async def test_if_none_match_still_reports_cache_state(
    client: AsyncClient, seeded_catalog: list[Any]
) -> None:
    etag = (await client.get("/api/v1/products")).headers["ETag"]

    # Повторный запрос в рамках одного ключа -> HIT
    assert (await client.get("/api/v1/products", headers={"If-None-Match": etag})).headers[
        "X-Cache"
    ] == "HIT"

    # Несовпадающий ETag -> полноценный 200
    stale = await client.get("/api/v1/products", headers={"If-None-Match": '"stale-etag"'})
    assert stale.status_code == 200
    assert stale.headers["ETag"] == etag


async def test_etag_changes_when_content_changes(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    before = await client.get("/api/v1/products")
    etag_before = before.headers["ETag"]

    resp = await client.patch(f"/api/v1/products/{seeded_catalog[0]['id']}", json={"price": 3.5})
    assert resp.status_code == 200

    after = await client.get("/api/v1/products")
    assert after.headers["ETag"] != etag_before
    assert after.json()[0]["price"] == 3.5


async def test_etag_on_product_detail(
    client: AsyncClient, seeded_catalog: list[dict[str, Any]]
) -> None:
    product_id = seeded_catalog[1]["id"]
    url = f"/api/v1/products/{product_id}"

    first = await client.get(url)
    assert first.status_code == 200
    etag = first.headers["ETag"]

    second = await client.get(url, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


async def test_etag_supports_if_none_match_star(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/products", headers={"If-None-Match": "*"})
    # Пустой каталог: '*' матчится на полученный ETag
    assert resp.status_code == 304
    assert resp.headers["X-Cache"] == "MISS"
