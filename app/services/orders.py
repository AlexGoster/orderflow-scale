from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import AppError, NotFoundError
from app.models import Order, OrderItem, OrderStatus
from app.schemas import OrderCreate
from app.services.products import get_products_by_ids


async def create_order(session: AsyncSession, user_id: int, payload: OrderCreate) -> Order:
    """Создание заказа.

    Все товары позиции запрашиваются **одним** ``WHERE id IN (...)``,
    а не по ``session.get`` на каждую позицию (N+1).
    """
    needed: dict[int, int] = {}
    for item in payload.items:
        needed[item.product_id] = needed.get(item.product_id, 0) + item.quantity

    products = await get_products_by_ids(session, needed)

    missing = sorted(set(needed) - set(products))
    if missing:
        raise NotFoundError(f"Product {missing[0]} not found")

    for product_id, quantity in needed.items():
        product = products[product_id]
        if product.stock < quantity:
            raise AppError(
                f"Insufficient stock for {product.sku}: {product.stock} left",
                code="insufficient_stock",
                status_code=409,
            )

    order = Order(user_id=user_id, total=Decimal("0"))
    session.add(order)
    await session.flush()

    total = Decimal("0")
    for product_id in sorted(needed):
        quantity = needed[product_id]
        product = products[product_id]
        product.stock -= quantity
        total += Decimal(str(product.price)) * quantity
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=quantity,
                unit_price=product.price,
            )
        )

    order.total = total
    await session.commit()

    # Возвращаем заказ с позициями: один явный SELECT ... options(selectinload),
    # а не отложенная догрузка items при сериализации ответа.
    refreshed = await session.scalar(
        select(Order).where(Order.id == order.id).options(selectinload(Order.items))
    )
    if refreshed is None:
        raise AppError("Order disappeared after commit", code="order_missing", status_code=500)
    return refreshed


async def list_orders(session: AsyncSession, user_id: int) -> list[Order]:
    """Список заказов: 2 запроса (orders + items) независимо от количества."""
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc(), Order.id.desc())
    )
    result = await session.scalars(stmt)
    return list(result)


async def get_order(session: AsyncSession, user_id: int, order_id: int) -> Order:
    """Один заказ: ``joinedload`` даёт ровно 1 запрос (items — одним JOIN)."""
    stmt = (
        select(Order)
        .where(Order.id == order_id, Order.user_id == user_id)
        .options(joinedload(Order.items))
    )
    order = await session.scalar(stmt)
    if order is None:
        raise NotFoundError("Order not found")
    return order


async def change_order_status(session: AsyncSession, order_id: int, status: OrderStatus) -> Order:
    stmt = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    order = await session.scalar(stmt)
    if order is None:
        raise NotFoundError("Order not found")
    order.status = status
    await session.commit()
    return order
