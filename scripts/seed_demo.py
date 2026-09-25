"""Заполняет каталог демо-данными: ``python -m scripts.seed_demo --products 300``."""

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.core.database import dispose_engine, get_session_factory
from app.models import Product, User

CATEGORIES = ("books", "toys", "tools", "garden", "sports")


async def seed(products: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(Product))
        await session.execute(delete(User))
        await session.commit()

        session.add_all(
            Product(
                sku=f"DEMO-{index:05d}",
                name=f"Demo product {index:05d}",
                category=CATEGORIES[index % len(CATEGORIES)],
                price=Decimal(str(round(5 + (index % 95) * 1.5, 2))),
                stock=100 + index,
                is_active=True,
            )
            for index in range(1, products + 1)
        )
        await session.commit()

        total = int(await session.scalar(select(func.count(Product.id))) or 0)
        print(f"seeded {total} products")

    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(seed(args.products))
