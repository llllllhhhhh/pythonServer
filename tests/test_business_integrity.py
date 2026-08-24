from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.admin import (
    check_in_order,
    complete_order,
    issue_order_qr,
    review_order_contract,
    schedule_order_pickup,
    start_order_trip,
)
from app.api.commerce import mock_pay, study_check_in, update_product
from app.api.travel_orders import (
    _review_eligible_order,
    confirm_travel_pickup,
    create_route_review,
    exchange_travel_route,
    sign_travel_contract,
    submit_travel_pickup_info,
)
from app.core.database import Base
from app.core.order_status import TravelContractStatus, TravelFulfillmentStatus
from app.models import (
    PointLedger,
    PointLot,
    PointRule,
    StudyOrder,
    StudyProduct,
    TravelOrder,
    TravelOrderAuditLog,
    TravelRoute,
    TravelVerificationRecord,
    UserAccount,
)
from app.schemas.api import (
    ContractReviewPayload,
    ContractSignPayload,
    RouteExchangePayload,
    TravelPickupInfoPayload,
    TravelPickupSchedulePayload,
    TravelRouteReviewCreate,
)
from app.schemas.commerce import StudyProductPayload
from app.services.point_service import PointService
from app.services.travel_order_service import cancel_travel_order, qr_token_hash


class BusinessIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = self.session_factory()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await self.engine.dispose()

    async def create_user(self, *, user_no: str = "U001", role: str = "user") -> UserAccount:
        user = UserAccount(
            user_no=user_no,
            phone=f"1550000{len(user_no):04d}",
            nickname=user_no,
            password_hash="unused",
            role=role,
            status="active",
            is_registered=True,
            points=0,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def grant_points(self, user: UserAccount, amount: int) -> None:
        locked = await PointService.lock_user(self.db, user.id)
        await PointService.credit_locked(
            self.db,
            locked,
            amount,
            biz_type="test_grant",
            biz_no=f"grant-{user.id}",
            idempotency_key=f"test-grant:{user.id}",
            remark="测试积分发放",
            expires_at=datetime.now() + timedelta(days=365),
        )
        await self.db.commit()

    async def test_exchange_is_idempotent_and_cancel_refunds_once(self) -> None:
        user = await self.create_user()
        route = TravelRoute(
            name="测试旅行路线",
            category="研学",
            days="3天2夜",
            price=100,
            stock=2,
            agency="测试服务方",
            status=True,
            review_status="approved",
        )
        self.db.add_all([route, PointRule(id=1, valid_days=365)])
        await self.db.commit()
        await self.grant_points(user, 300)

        first = await exchange_travel_route(
            route.id,
            RouteExchangePayload(),
            idempotency_key="same-request-key",
            current_user=user,
            db=self.db,
        )
        second = await exchange_travel_route(
            route.id,
            RouteExchangePayload(),
            idempotency_key="same-request-key",
            current_user=user,
            db=self.db,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(1, await self.db.scalar(select(func.count(TravelOrder.id))))
        self.assertEqual(200, (await self.db.get(UserAccount, user.id)).points)
        self.assertEqual(1, (await self.db.get(TravelRoute, route.id)).stock)
        self.assertEqual(
            1,
            await self.db.scalar(
                select(func.count(PointLedger.id)).where(PointLedger.biz_type == "travel_exchange")
            ),
        )

        cancelled = await cancel_travel_order(
            self.db,
            first.id,
            actor=user,
            reason="行程有变",
        )
        repeated = await cancel_travel_order(
            self.db,
            first.id,
            actor=user,
            reason="重复取消",
        )

        self.assertEqual(cancelled.id, repeated.id)
        self.assertEqual(TravelFulfillmentStatus.CANCELLED, repeated.fulfillment_status)
        self.assertTrue(repeated.points_refunded)
        self.assertTrue(repeated.stock_restored)
        self.assertEqual(300, (await self.db.get(UserAccount, user.id)).points)
        self.assertEqual(2, (await self.db.get(TravelRoute, route.id)).stock)
        self.assertEqual(
            1,
            await self.db.scalar(
                select(func.count(PointLedger.id)).where(PointLedger.biz_type == "travel_exchange_refund")
            ),
        )
        self.assertEqual(
            1,
            await self.db.scalar(
                select(func.count(TravelOrderAuditLog.id)).where(
                    TravelOrderAuditLog.event_type == "order_cancelled"
                )
            ),
        )

    async def test_point_debit_uses_earliest_expiring_lot(self) -> None:
        user = await self.create_user()
        locked = await PointService.lock_user(self.db, user.id)
        later = await PointService.credit_locked(
            self.db,
            locked,
            100,
            biz_type="grant",
            biz_no="later",
            idempotency_key="grant:later",
            expires_at=datetime.now() + timedelta(days=30),
        )
        sooner = await PointService.credit_locked(
            self.db,
            locked,
            60,
            biz_type="grant",
            biz_no="sooner",
            idempotency_key="grant:sooner",
            expires_at=datetime.now() + timedelta(days=5),
        )
        await self.db.flush()
        await PointService.debit_locked(
            self.db,
            locked,
            80,
            biz_type="test_expense",
            biz_no="expense-1",
            idempotency_key="expense:1",
        )
        await self.db.commit()

        later_lot = await self.db.scalar(select(PointLot).where(PointLot.source_ledger_id == later.id))
        sooner_lot = await self.db.scalar(select(PointLot).where(PointLot.source_ledger_id == sooner.id))
        self.assertEqual(0, sooner_lot.remaining_amount)
        self.assertEqual(80, later_lot.remaining_amount)
        self.assertEqual(80, locked.points)

    async def test_learning_checkin_grants_configured_points_once(self) -> None:
        user = await self.create_user()
        self.db.add(PointRule(id=1, checkin_score=3, purchase_score=0, invite_score=0, valid_days=30))
        await self.db.commit()

        first = await study_check_in(user=user, db=self.db)
        second = await study_check_in(user=user, db=self.db)
        refreshed = await self.db.get(UserAccount, user.id)
        await self.db.refresh(refreshed)

        self.assertTrue(first["checked"])
        self.assertTrue(second["checked"])
        self.assertEqual(1, first["checkin_days"])
        self.assertEqual(3, refreshed.points)
        self.assertEqual(
            1,
            await self.db.scalar(
                select(func.count(PointLedger.id)).where(PointLedger.biz_type == "learning_checkin")
            ),
        )

    async def test_study_purchase_grants_configured_points_once(self) -> None:
        user = await self.create_user()
        product = StudyProduct(
            name="积分测试课",
            product_type="course",
            price=10,
            stock=10,
            status=True,
            review_status="approved",
        )
        self.db.add_all([PointRule(id=1, purchase_score=8, checkin_score=0, invite_score=0, valid_days=30), product])
        await self.db.flush()
        order = StudyOrder(
            order_no="ST-POINT-1",
            user_id=user.id,
            product_id=product.id,
            product_name=product.name,
            product_type=product.product_type,
            amount=10,
            payment_status="pending",
        )
        self.db.add(order)
        await self.db.commit()

        await mock_pay(order.id, user=user, db=self.db)
        await mock_pay(order.id, user=user, db=self.db)
        refreshed = await self.db.get(UserAccount, user.id)
        await self.db.refresh(refreshed)

        self.assertEqual(8, refreshed.points)
        self.assertEqual(
            1,
            await self.db.scalar(
                select(func.count(PointLedger.id)).where(PointLedger.biz_type == "study_purchase")
            ),
        )

    async def test_review_requires_completed_trip(self) -> None:
        user = await self.create_user()
        route = TravelRoute(name="待出行路线", price=100, stock=1, status=True, review_status="approved")
        self.db.add(route)
        await self.db.flush()
        order = TravelOrder(
            order_no="TR-REVIEW-1",
            user_id=user.id,
            user_no=user.user_no,
            route_id=route.id,
            idempotency_key="review-order",
            order_type="points_exchange",
            title=route.name,
            user_name=user.nickname,
            phone=user.phone,
            fulfillment_status=TravelFulfillmentStatus.INFO_PENDING,
        )
        self.db.add(order)
        await self.db.commit()

        orders, _, eligible = await _review_eligible_order(self.db, route.id, user)
        self.assertEqual(1, len(orders))
        self.assertIsNone(eligible)

        order.fulfillment_status = TravelFulfillmentStatus.COMPLETED
        await self.db.commit()
        _, _, eligible = await _review_eligible_order(self.db, route.id, user)
        self.assertEqual(order.id, eligible.id)

    async def test_completed_order_cannot_return_to_contract_review(self) -> None:
        user = await self.create_user()
        order = TravelOrder(
            order_no="TR-CONTRACT-GUARD",
            user_id=user.id,
            user_no=user.user_no,
            idempotency_key="contract-guard",
            order_type="points_exchange",
            title="状态守卫路线",
            user_name=user.nickname,
            phone=user.phone,
            contract_status=TravelContractStatus.REJECTED,
            fulfillment_status=TravelFulfillmentStatus.COMPLETED,
        )
        self.db.add(order)
        await self.db.commit()

        with self.assertRaises(HTTPException) as caught:
            await sign_travel_contract(
                order.id,
                ContractSignPayload(
                    signer_name="测试用户",
                    signer_phone="15500000000",
                    id_no="TEST-ID",
                    travel_date="2026-09-01",
                    signature_data="x" * 60,
                ),
                current_user=user,
                db=self.db,
            )

        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual(TravelFulfillmentStatus.COMPLETED, order.fulfillment_status)

    async def test_issued_qr_cannot_be_invalidated_by_pickup_resubmission(self) -> None:
        user = await self.create_user()
        order = TravelOrder(
            order_no="TR-PICKUP-GUARD",
            user_id=user.id,
            user_no=user.user_no,
            idempotency_key="pickup-guard",
            order_type="points_exchange",
            title="接送守卫路线",
            user_name=user.nickname,
            phone=user.phone,
            contract_status=TravelContractStatus.APPROVED,
            fulfillment_status=TravelFulfillmentStatus.QR_ISSUED,
            qr_token="existing-token",
        )
        self.db.add(order)
        await self.db.commit()

        with self.assertRaises(HTTPException) as caught:
            await submit_travel_pickup_info(
                order.id,
                TravelPickupInfoPayload(
                    pickup_address="测试地址",
                    emergency_contact="联系人",
                    emergency_phone="15500000000",
                ),
                current_user=user,
                db=self.db,
            )

        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual(TravelFulfillmentStatus.QR_ISSUED, order.fulfillment_status)
        self.assertEqual("existing-token", order.qr_token)

    async def test_admin_product_update_cannot_override_review_status(self) -> None:
        product = StudyProduct(
            name="待审课程",
            product_type="material",
            price=10,
            stock=1,
            status=False,
            review_status="pending",
            reject_reason="",
        )
        self.db.add(product)
        await self.db.commit()

        await update_product(
            product.id,
            StudyProductPayload(
                name="修改后的待审课程",
                product_type="material",
                price=10,
                stock=1,
                status=True,
                review_status="approved",
            ),
            db=self.db,
        )
        await self.db.refresh(product)

        self.assertEqual("pending", product.review_status)
        self.assertFalse(product.status)

    async def test_contract_and_qr_actions_create_audit_records(self) -> None:
        user = await self.create_user()
        admin = await self.create_user(user_no="ADMIN001", role="admin")
        order = TravelOrder(
            order_no="TR-AUDIT-1",
            user_id=user.id,
            user_no=user.user_no,
            idempotency_key="audit-order",
            order_type="points_exchange",
            title="审计测试路线",
            user_name=user.nickname,
            phone=user.phone,
            travel_date=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
            contract_status=TravelContractStatus.PENDING,
            fulfillment_status=TravelFulfillmentStatus.CONTRACT_REVIEWING,
        )
        self.db.add(order)
        await self.db.commit()

        await review_order_contract(
            order.id,
            ContractReviewPayload(approved=True),
            admin_user=admin,
            db=self.db,
        )
        order.fulfillment_status = TravelFulfillmentStatus.USER_CONFIRMED
        await self.db.commit()
        issued = await issue_order_qr(order.id, admin_user=admin, db=self.db)
        token = issued.qr_token
        self.assertTrue(token)
        self.assertGreater(issued.qr_expires_at, datetime.now())

        checked = await check_in_order(
            order.id,
            token,
            device="scanner-test",
            location="test-gate",
            admin_user=admin,
            db=self.db,
        )
        self.assertEqual(TravelFulfillmentStatus.CHECKED_IN, checked.fulfillment_status)
        self.assertEqual("", checked.qr_token)

        with self.assertRaises(HTTPException):
            await check_in_order(
                order.id,
                token,
                device="scanner-test",
                location="test-gate",
                admin_user=admin,
                db=self.db,
            )

        records = list(
            await self.db.scalars(
                select(TravelVerificationRecord).where(TravelVerificationRecord.order_id == order.id)
            )
        )
        self.assertEqual(3, len(records))
        self.assertTrue(all(item.token_hash == qr_token_hash(token) for item in records))
        self.assertTrue(all(item.token_hash != token for item in records))
        event_types = set(
            await self.db.scalars(
                select(TravelOrderAuditLog.event_type).where(TravelOrderAuditLog.order_id == order.id)
            )
        )
        self.assertTrue({"contract_approved", "qr_issued", "travel_checked_in"}.issubset(event_types))

    async def test_e2e_002_travel_exchange_to_review_lifecycle(self) -> None:
        user = await self.create_user(user_no="U-E2E-TRAVEL")
        admin = await self.create_user(user_no="ADMIN-E2E", role="admin")
        await self.grant_points(user, 500)
        route = TravelRoute(
            name="E2E Travel Route",
            category="研学",
            days="3天2夜",
            price=120,
            stock=1,
            status=True,
            review_status="approved",
            agency="E2E Agency",
        )
        self.db.add(route)
        await self.db.commit()

        order = await exchange_travel_route(
            route.id,
            RouteExchangePayload(travel_date=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")),
            current_user=user,
            db=self.db,
            idempotency_key="travel-e2e-idempotency-001",
        )
        with patch("app.api.travel_orders._save_contract_signature", new=AsyncMock(return_value="/signed/e2e.png")):
            await sign_travel_contract(
                order.id,
                ContractSignPayload(
                    signer_name="Automation User",
                    signer_phone=user.phone,
                    id_no="ID-E2E-001",
                    travel_date=order.travel_date,
                    signature_data="data:image/png;base64," + "a" * 80,
                ),
                current_user=user,
                db=self.db,
            )
        reviewed = await review_order_contract(
            order.id,
            ContractReviewPayload(approved=True),
            admin_user=admin,
            db=self.db,
        )
        self.assertEqual(TravelFulfillmentStatus.INFO_PENDING, reviewed.fulfillment_status)

        await submit_travel_pickup_info(
            order.id,
            TravelPickupInfoPayload(
                pickup_address="Airport T2",
                traveler_count=1,
                emergency_contact="Emergency",
                emergency_phone="15500000002",
            ),
            current_user=user,
            db=self.db,
        )
        await schedule_order_pickup(
            order.id,
            TravelPickupSchedulePayload(
                pickup_time="2026-08-20 09:00",
                pickup_location="Airport T2 Gate 3",
                driver_name="Driver",
                driver_phone="15500000003",
                vehicle_no="TEST-001",
            ),
            admin_user=admin,
            db=self.db,
        )
        confirmed = await confirm_travel_pickup(order.id, current_user=user, db=self.db)
        self.assertEqual(TravelFulfillmentStatus.USER_CONFIRMED, confirmed.fulfillment_status)
        issued = await issue_order_qr(order.id, admin_user=admin, db=self.db)
        token = issued.qr_token
        await check_in_order(order.id, token, device="e2e-scanner", location="e2e-gate", admin_user=admin, db=self.db)
        await start_order_trip(order.id, admin_user=admin, db=self.db)
        completed = await complete_order(order.id, admin_user=admin, db=self.db)
        self.assertEqual(TravelFulfillmentStatus.COMPLETED, completed.fulfillment_status)

        review = await create_route_review(
            route.id,
            TravelRouteReviewCreate(order_id=order.id, rating=5, content="Lifecycle verified"),
            current_user=user,
            db=self.db,
        )
        self.assertEqual(order.id, review.order_id)
        events = set(
            await self.db.scalars(
                select(TravelOrderAuditLog.event_type).where(TravelOrderAuditLog.order_id == order.id)
            )
        )
        self.assertTrue(
            {
                "contract_signed",
                "contract_approved",
                "pickup_info_submitted",
                "pickup_scheduled",
                "pickup_confirmed_by_user",
                "qr_issued",
                "travel_checked_in",
                "trip_started",
                "order_completed",
            }.issubset(events)
        )


if __name__ == "__main__":
    unittest.main()
