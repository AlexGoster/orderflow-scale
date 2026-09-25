from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Product
from app.schemas import ProductCreate, ProductUpdate


async def list_products(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 50,
    category: str | None = None,
    active_only: bool = True,
) -> list[Product]:
    """Список товаров: одна выборка под составной индекс (is_active, category)."""
    conditions: list[ColumnElement[bool]] = []
    if active_only:
        conditions.append(Product.is_active.is_(True))
    if category is not None:
        conditions.append(Product.category == category)

    stmt = select(Product)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(Product.id).offset(offset).limit(limit)
    result = await session.scalars(stmt)
    return list(result)


async def get_product(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found")
    return product


async def get_products_by_ids(
    session: AsyncSession, product_ids: Iterable[int]
) -> dict[int, Product]:
    """Батч-выборка: 1 запрос вместо N ``session.get`` (устранение N+1)."""
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    rows = await session.scalars(select(Product).where(Product.id.in_(ids)))
    return {product.id: product for product in rows}


async def create_product(session: AsyncSession, payload: ProductCreate) -> Product:
    existing = await session.scalar(select(Product).where(Product.sku == payload.sku))
    if existing is not None:
        raise ConflictError(f"SKU {payload.sku} already exists")
    product = Product(**payload.model_dump())
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def update_product(session: AsyncSession, product_id: int, payload: ProductUpdate) -> Product:
    product = await get_product(session, product_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await session.commit()
    await session.refresh(product)
    return product
