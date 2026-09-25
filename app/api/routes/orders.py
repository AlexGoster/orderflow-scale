from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.cache import ProductCache, get_product_cache
from app.core.database import get_session
from app.models import Order, OrderStatus, User
from app.schemas import OrderCreate, OrderRead
from app.services import orders as orders_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    cache: ProductCache = Depends(get_product_cache),
) -> Order:
    order = await orders_service.create_order(session, user.id, payload)
    # Склад в товарах изменился -> каталог в кэше устарел.
    await cache.invalidate()
    return order


@router.get("", response_model=list[OrderRead])
async def list_orders(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Order]:
    return await orders_service.list_orders(session, user.id)


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Order:
    return await orders_service.get_order(session, user.id, order_id)


@router.patch("/{order_id}/status", response_model=OrderRead)
async def change_status(
    order_id: int,
    new_status: OrderStatus,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Order:
    return await orders_service.change_order_status(session, order_id, new_status)
