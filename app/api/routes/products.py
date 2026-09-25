import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import ProductCache, get_product_cache
from app.core.database import get_session
from app.models import Product
from app.schemas import ProductCreate, ProductRead, ProductUpdate
from app.services import products as products_service

router = APIRouter(prefix="/products", tags=["products"])


def canonical_body(items: list[ProductRead]) -> tuple[str, list[dict[str, Any]]]:
    """Каноничное представление страницы: одинаковый порядок ключей -> одинаковый ETag."""
    payload = [item.model_dump(mode="json") for item in items]
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return body, payload


def make_etag(body: str) -> str:
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'


def if_none_match_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    candidates = [part.strip() for part in header.split(",")]
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates


def _load_page(blob: str) -> tuple[str, list[ProductRead]]:
    decoded: dict[str, Any] = json.loads(blob)
    products = [ProductRead.model_validate(item) for item in decoded["payload"]]
    return str(decoded["etag"]), products


def _dump_page(products: list[ProductRead]) -> tuple[str, str]:
    body, payload = canonical_body(products)
    etag = make_etag(body)
    return etag, json.dumps({"etag": etag, "payload": payload}, separators=(",", ":"))


@router.get("", response_model=list[ProductRead])
async def list_products(
    request: Request,
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    category: str | None = Query(None, min_length=1, max_length=64),
    session: AsyncSession = Depends(get_session),
    cache: ProductCache = Depends(get_product_cache),
) -> list[ProductRead] | Response:
    """Каталог: cache-aside + ETag/If-None-Match."""
    key = cache.list_key(offset=offset, limit=limit, category=category)
    cached = await cache.get(key)

    if cached is None:
        rows = await products_service.list_products(
            session, offset=offset, limit=limit, category=category
        )
        products = [ProductRead.model_validate(row) for row in rows]
        etag, blob = _dump_page(products)
        await cache.set(key, blob)
        cache_state = "MISS"
    else:
        etag, products = _load_page(cached)
        cache_state = "HIT"

    if if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "X-Cache": cache_state},
        )

    response.headers["ETag"] = etag
    response.headers["X-Cache"] = cache_state
    return products


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: int,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    cache: ProductCache = Depends(get_product_cache),
) -> ProductRead | Response:
    key = cache.item_key(product_id)
    cached = await cache.get(key)

    if cached is None:
        product = await products_service.get_product(session, product_id)
        read = ProductRead.model_validate(product)
        etag, blob = _dump_page([read])
        await cache.set(key, blob)
        cache_state = "MISS"
    else:
        decoded: dict[str, Any] = json.loads(cached)
        read = ProductRead.model_validate(decoded["payload"][0])
        etag = str(decoded["etag"])
        cache_state = "HIT"

    if if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "X-Cache": cache_state},
        )

    response.headers["ETag"] = etag
    response.headers["X-Cache"] = cache_state
    return read


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    session: AsyncSession = Depends(get_session),
    cache: ProductCache = Depends(get_product_cache),
) -> Product:
    product = await products_service.create_product(session, payload)
    await cache.invalidate()
    return product


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    session: AsyncSession = Depends(get_session),
    cache: ProductCache = Depends(get_product_cache),
) -> Product:
    product = await products_service.update_product(session, product_id, payload)
    await cache.invalidate()
    return product
