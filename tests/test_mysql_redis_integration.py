from __future__ import annotations

import asyncio
import os
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import delete, func, select

from app.core.cache import redis
from app.core.database import SessionLocal
from app.models import CommerceOrder, CommerceOrderItem, SchoolSite, StudyProduct, UserAccount
from app.schemas.order import OrderCreateItem, OrderCreateRequest
from app.services.order_service import OrderService


@unittest.skipUnless(os.getenv("RUN_DATABASE_INTEGRATION") == "1", "requires dedicated MySQL and Redis test services")
class MySqlRedisConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mysql_and_redis_prevent_duplicate_and_oversold_orders(self) -> None:
        suffix = uuid4().hex[:12]
        phone_base = int(uuid4().hex[:8], 16) % 99_999_990
        async with SessionLocal() as db:
            school = SchoolSite(name=f"CI School {suffix}", review_status="approved", status=True)
            users = [
                UserAccount(
                    user_no=f"CI-{suffix}-{index}",
                    phone=f"155{phone_base + index:08d}",
                    nickname=f"CI User {index}",
                    password_hash="unused",
                    role="user",
                    status="active",
                    is_registered=True,
                    balance=Decimal("100.00"),
                )
                for index in range(1, 4)
            ]
            db.add_all([school, *users])
            await db.flush()
            product = StudyProduct(
                school_id=school.id,
                name=f"CI Product {suffix}",
                product_type="community",
                price=Decimal("10.00"),
                original_price=Decimal("10.00"),
                stock=2,
                review_status="approved",
                status=True,
            )
            db.add(product)
            await db.commit()
            user_ids = [item.id for item in users]
            product_id = product.id

        same_key = f"ci-idem-{suffix}"

        async def create(user_id: int, key: str):
            async with SessionLocal() as db:
                user = await db.get(UserAccount, user_id)
                return await OrderService.create_order(
                    db,
                    user,
                    OrderCreateRequest(
                        items=[OrderCreateItem(product_id=product_id)],
                        payment_method="balance",
                        idempotency_key=key,
                    ),
                )

        with patch("app.services.order_service.enqueue_order_payment_timeout"):
            duplicate_results = await asyncio.gather(
                create(user_ids[0], same_key),
                create(user_ids[0], same_key),
                return_exceptions=True,
            )
        successful_duplicates = [item for item in duplicate_results if isinstance(item, CommerceOrder)]
        self.assertGreaterEqual(len(successful_duplicates), 1)

        async with SessionLocal() as db:
            duplicate_count = await db.scalar(
                select(func.count(CommerceOrder.id)).where(
                    CommerceOrder.user_id == user_ids[0], CommerceOrder.idempotency_key == same_key
                )
            )
            self.assertEqual(1, duplicate_count)

        async with SessionLocal() as db:
            product = await db.get(StudyProduct, product_id)
            product.stock = 1
            await db.commit()
        await redis.raw.set(f"stock:study_product:{product_id}", 1)

        race_keys = [f"ci-stock-{suffix}-1", f"ci-stock-{suffix}-2"]
        with patch("app.services.order_service.enqueue_order_payment_timeout"):
            stock_results = await asyncio.gather(
                create(user_ids[1], race_keys[0]),
                create(user_ids[2], race_keys[1]),
                return_exceptions=True,
            )
        self.assertEqual(1, sum(isinstance(item, CommerceOrder) for item in stock_results))

        async with SessionLocal() as db:
            product = await db.get(StudyProduct, product_id)
            stock_order_count = await db.scalar(
                select(func.count(CommerceOrder.id)).where(CommerceOrder.idempotency_key.in_(race_keys))
            )
            self.assertEqual(0, product.stock)
            self.assertEqual(1, stock_order_count)
        self.assertEqual(0, int(await redis.raw.get(f"stock:study_product:{product_id}")))

        async with SessionLocal() as db:
            order_ids = list(
                await db.scalars(select(CommerceOrder.id).where(CommerceOrder.user_id.in_(user_ids)))
            )
            if order_ids:
                await db.execute(delete(CommerceOrderItem).where(CommerceOrderItem.order_id.in_(order_ids)))
                await db.execute(delete(CommerceOrder).where(CommerceOrder.id.in_(order_ids)))
            await db.execute(delete(StudyProduct).where(StudyProduct.id == product_id))
            await db.execute(delete(UserAccount).where(UserAccount.id.in_(user_ids)))
            await db.execute(delete(SchoolSite).where(SchoolSite.id == school.id))
            await db.commit()
        await redis.raw.delete(
            f"stock:study_product:{product_id}",
            f"idem:order:{user_ids[0]}:{same_key}",
            f"idem:order:{user_ids[0]}:{same_key}:result",
            *[f"idem:order:{user_ids[index]}:{race_keys[index - 1]}" for index in (1, 2)],
            *[f"idem:order:{user_ids[index]}:{race_keys[index - 1]}:result" for index in (1, 2)],
        )
