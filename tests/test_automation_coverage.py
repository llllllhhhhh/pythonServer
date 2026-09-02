from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.api.admin import (
    adjust_user_points,
    adjust_user_wallet,
    create_announcement,
    delete_announcement,
    review_registration,
    review_school_site,
    toggle_announcement,
    update_user_status,
    update_announcement,
)
from app.api.commerce import merchant_create_product, review_product, wechat_pay_notification
from app.api.merchant import apply_school
from app.api.support import (
    admin_messages,
    close_conversation,
    create_order_conversation,
    merchant_messages,
    upload_support_image,
    user_messages,
)
from app.core.database import Base
from app.core.login_guard import _client_ip, _login_key, ensure_login_allowed
from app.core.slider_captcha import (
    SLIDER_CAPTCHA_CHALLENGE_PREFIX,
    SLIDER_CAPTCHA_TICKET_PREFIX,
    consume_slider_ticket,
    create_slider_challenge,
    verify_slider_challenge,
)
from app.models import (
    CommerceOrderItem,
    PlatformAnnouncement,
    SchoolSite,
    StudyProduct,
    SupportConversation,
    SupportMessage,
    UserAccount,
    UserEntitlement,
    UserSession,
    WalletTransaction,
)
from app.schemas.api import (
    AnnouncementCreate,
    AnnouncementUpdate,
    PointsAdjustPayload,
    RegistrationReviewPayload,
    SchoolApplicationPayload,
    SchoolReviewPayload,
    StatusUpdatePayload,
    WalletAdjustPayload,
)
from app.schemas.commerce import StudyProductPayload, StudyProductReviewPayload
from app.schemas.order import OrderCreateItem, OrderCreateRequest
from app.services.order_service import OrderService


class FakeOrderRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.stock: dict[int, int] = {}

    async def get_json(self, key: str):
        return self.values.get(key)

    async def set_json(self, key: str, value: object, _ttl: int):
        self.values[key] = value

    async def setnx(self, key: str, value: object, _ttl: int) -> bool:
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def set_stock_if_absent(self, product_id: int, stock: int):
        self.stock.setdefault(product_id, stock)

    async def decr_stock(self, product_id: int, quantity: int) -> int:
        self.stock[product_id] -= quantity
        return self.stock[product_id]

    async def incr_stock(self, product_id: int, quantity: int) -> int:
        self.stock[product_id] = self.stock.get(product_id, 0) + quantity
        return self.stock[product_id]


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = self.session_factory()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await self.engine.dispose()

    async def create_user(self, *, balance: Decimal = Decimal("0.00"), user_no: str = "U-AUTO-1") -> UserAccount:
        user = UserAccount(
            user_no=user_no,
            phone="15500000001",
            nickname="Automation User",
            password_hash="unused",
            role="user",
            status="active",
            is_registered=True,
            balance=balance,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def create_school_product(self, *, stock: int = 3, price: Decimal = Decimal("19.90")):
        school = SchoolSite(name="Automation School", review_status="approved", status=True)
        self.db.add(school)
        await self.db.flush()
        product = StudyProduct(
            school_id=school.id,
            name="Automation Course",
            product_type="community",
            price=price,
            original_price=price,
            stock=stock,
            sales=0,
            review_status="approved",
            status=True,
        )
        self.db.add(product)
        await self.db.commit()
        return school, product


class StandardOrderAutomationTests(DatabaseTestCase):
    async def test_wechat_callback_marks_order_paid_once(self) -> None:
        user = await self.create_user()
        _, product = await self.create_school_product(stock=2, price=Decimal("0.01"))
        payload = OrderCreateRequest(
            items=[OrderCreateItem(product_id=product.id, quantity=1)],
            payment_method="wechat",
            idempotency_key="wechat-callback-idem-001",
        )
        fake_redis = FakeOrderRedis()
        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ):
            order = await OrderService.create_order(self.db, user, payload)

        body = b"{}"

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            receive,
        )
        transaction = {
            "appid": "wx-test",
            "mchid": "mch-test",
            "out_trade_no": order.order_no,
            "transaction_id": "WX-TRANSACTION-001",
            "trade_state": "SUCCESS",
            "amount": {"total": 1, "currency": "CNY"},
        }
        with patch("app.api.commerce.verify_notification_signature"), patch(
            "app.api.commerce.decrypt_notification", return_value=transaction
        ), patch.multiple(
            "app.api.commerce.settings", wechat_mini_app_id="wx-test", wechat_mch_id="mch-test"
        ):
            first = await wechat_pay_notification(request, db=self.db)
            request = Request(
                {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
                receive,
            )
            second = await wechat_pay_notification(request, db=self.db)

        self.assertEqual("SUCCESS", first["code"])
        self.assertEqual("SUCCESS", second["code"])
        await self.db.refresh(order)
        await self.db.refresh(product)
        self.assertEqual("paid", order.payment_status)
        self.assertEqual("WX-TRANSACTION-001", order.transaction_id)
        self.assertEqual(1, await self.db.scalar(select(func.count(UserEntitlement.id))))
        self.assertEqual(1, product.sales)

    async def test_mark_paid_is_idempotent_and_serialized(self) -> None:
        user = await self.create_user()
        _, product = await self.create_school_product(stock=2, price=Decimal("25.00"))
        payload = OrderCreateRequest(
            items=[OrderCreateItem(product_id=product.id, quantity=1)],
            payment_method="wechat",
            idempotency_key="standard-paid-idem-001",
        )
        fake_redis = FakeOrderRedis()
        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ):
            order = await OrderService.create_order(self.db, user, payload)
            first = await OrderService.mark_order_paid(
                self.db, order, payment_method="wechat", transaction_id="WX-TX-001"
            )
            second = await OrderService.mark_order_paid(
                self.db, first, payment_method="wechat", transaction_id="WX-TX-001-RETRY"
            )

        self.assertEqual(first.id, second.id)
        self.assertEqual("WX-TX-001", second.transaction_id)
        self.assertEqual(1, await self.db.scalar(select(func.count(UserEntitlement.id))))
        await self.db.refresh(product)
        self.assertEqual(1, product.sales)

    async def test_create_is_idempotent_and_balance_payment_grants_once(self) -> None:
        user = await self.create_user(balance=Decimal("100.00"))
        _, product = await self.create_school_product(stock=2, price=Decimal("25.00"))
        payload = OrderCreateRequest(
            items=[OrderCreateItem(product_id=product.id, quantity=1)],
            payment_method="balance",
            idempotency_key="standard-order-idem-001",
        )
        fake_redis = FakeOrderRedis()

        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ) as enqueue:
            order = await OrderService.create_order(self.db, user, payload)
            duplicate = await OrderService.create_order(self.db, user, payload)
            paid = await OrderService.pay_with_balance(self.db, user, order)
            paid_again = await OrderService.pay_with_balance(self.db, user, paid)

        self.assertEqual(order.id, duplicate.id)
        self.assertEqual(order.id, paid_again.id)
        self.assertEqual("paid", paid.payment_status)
        self.assertEqual(1, enqueue.call_count)
        self.assertEqual(Decimal("75.00"), user.balance)
        self.assertEqual(1, await self.db.scalar(select(func.count(UserEntitlement.id))))
        self.assertEqual(1, await self.db.scalar(select(func.count(WalletTransaction.id))))
        await self.db.refresh(product)
        self.assertEqual(1, product.stock)
        self.assertEqual(1, product.sales)

    async def test_cancel_pending_order_restores_database_and_cache_stock_once(self) -> None:
        user = await self.create_user(balance=Decimal("100.00"))
        _, product = await self.create_school_product(stock=2)
        payload = OrderCreateRequest(
            items=[OrderCreateItem(product_id=product.id, quantity=1)],
            payment_method="balance",
            idempotency_key="standard-order-cancel-001",
        )
        fake_redis = FakeOrderRedis()
        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ):
            order = await OrderService.create_order(self.db, user, payload)
            changed = await OrderService.cancel_unpaid_order(self.db, order, "test timeout")
            changed_again = await OrderService.cancel_unpaid_order(self.db, order, "test timeout")

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        await self.db.refresh(product)
        self.assertEqual(2, product.stock)
        self.assertEqual(2, fake_redis.stock[product.id])
        item = await self.db.scalar(select(CommerceOrderItem).where(CommerceOrderItem.order_id == order.id))
        self.assertFalse(item.stock_deducted)


class SliderCaptchaAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def test_challenge_ticket_is_scoped_and_single_use(self) -> None:
        cache: dict[str, object] = {}

        async def cache_get(key: str):
            return cache.get(key)

        async def cache_set(key: str, value: object, ttl: int):
            self.assertGreater(ttl, 0)
            cache[key] = value

        async def cache_delete(key: str):
            cache.pop(key, None)

        db = SimpleNamespace(get=AsyncMock(return_value=None))
        with patch("app.core.slider_captcha.cache_get_json", side_effect=cache_get), patch(
            "app.core.slider_captcha.cache_set_json", side_effect=cache_set
        ), patch("app.core.slider_captcha.cache_delete", side_effect=cache_delete):
            challenge = await create_slider_challenge(db)
            self.assertIn(f"{SLIDER_CAPTCHA_CHALLENGE_PREFIX}{challenge['challenge_id']}", cache)

            verified = await verify_slider_challenge(challenge["challenge_id"], challenge["target_x"], "merchant")
            ticket = verified["ticket"]
            self.assertIn(f"{SLIDER_CAPTCHA_TICKET_PREFIX}{ticket}", cache)

            with self.assertRaises(HTTPException):
                await verify_slider_challenge(challenge["challenge_id"], challenge["target_x"], "merchant")
            with self.assertRaises(HTTPException):
                await consume_slider_ticket(db, ticket, "admin")
            with self.assertRaises(HTTPException):
                await consume_slider_ticket(db, ticket, "merchant")

            second = await create_slider_challenge(db)
            second_ticket = (await verify_slider_challenge(second["challenge_id"], second["target_x"], "merchant"))["ticket"]
            await consume_slider_ticket(db, second_ticket, "merchant")
            with self.assertRaises(HTTPException):
                await consume_slider_ticket(db, second_ticket, "merchant")


class AdminAndSupportAutomationTests(DatabaseTestCase):
    async def test_admin_user_review_wallet_points_and_session_revocation(self) -> None:
        user = await self.create_user(balance=Decimal("10.00"))
        user.status = "pending"
        user.is_registered = False
        admin = UserAccount(
            user_no="ADMIN-AUTO-1",
            phone="15500000002",
            nickname="Automation Admin",
            password_hash="unused",
            role="admin",
            status="active",
            is_registered=True,
        )
        self.db.add(admin)
        await self.db.commit()
        user_id = user.id
        admin_id = admin.id

        reviewed = await review_registration(
            user_id, RegistrationReviewPayload(approved=True), db=self.db
        )
        self.assertEqual("active", reviewed.status)
        self.assertTrue(user.is_registered)

        await adjust_user_wallet(
            user_id,
            WalletAdjustPayload(amount=Decimal("5.50"), direction="income", remark="automation"),
            db=self.db,
        )
        self.assertEqual(Decimal("15.50"), user.balance)
        self.assertEqual(1, await self.db.scalar(select(func.count(WalletTransaction.id))))
        with self.assertRaises(HTTPException) as raised:
            await adjust_user_wallet(
                user_id,
                WalletAdjustPayload(amount=Decimal("20.00"), direction="expense"),
                db=self.db,
            )
        self.assertEqual(400, raised.exception.status_code)
        await self.db.rollback()
        user = await self.db.get(UserAccount, user_id)
        admin = await self.db.get(UserAccount, admin_id)

        await adjust_user_points(
            user_id,
            PointsAdjustPayload(amount=8, direction="income", remark="automation"),
            admin_user=admin,
            db=self.db,
        )
        await adjust_user_points(
            user_id,
            PointsAdjustPayload(amount=3, direction="expense", remark="automation"),
            admin_user=admin,
            db=self.db,
        )
        self.assertEqual(5, user.points)

        self.db.add(
            UserSession(
                token="automation-user-session",
                user_id=user_id,
                role="user",
                expires_at=datetime.now() + timedelta(hours=1),
            )
        )
        await self.db.commit()
        disabled = await update_user_status(
            user_id, StatusUpdatePayload(status="disabled"), db=self.db
        )
        self.assertEqual("disabled", disabled.status)
        self.assertIsNone(await self.db.get(UserSession, "automation-user-session"))

    async def test_announcement_crud_and_status(self) -> None:
        created = await create_announcement(
            AnnouncementCreate(title="Automation announcement", content="Initial content"), db=self.db
        )
        updated = await update_announcement(
            created.id,
            AnnouncementUpdate(title="Updated announcement", content="Updated content"),
            db=self.db,
        )
        toggled = await toggle_announcement(created.id, True, db=self.db)
        self.assertEqual("Updated announcement", updated.title)
        self.assertTrue(toggled.status)
        await delete_announcement(created.id, db=self.db)
        self.assertIsNone(await self.db.get(PlatformAnnouncement, created.id))

    async def test_support_rest_scopes_messages_and_clears_unread_counts(self) -> None:
        user = await self.create_user()
        school, product = await self.create_school_product()
        order_payload = OrderCreateRequest(
            items=[OrderCreateItem(product_id=product.id)],
            payment_method="balance",
            idempotency_key="support-order-idem-001",
        )
        fake_redis = FakeOrderRedis()
        user.balance = Decimal("100.00")
        await self.db.commit()
        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ):
            order = await OrderService.create_order(self.db, user, order_payload)
            await OrderService.pay_with_balance(self.db, user, order)

        data = await create_order_conversation(
            {"user_id": user.user_no, "user_name": user.nickname, "order_id": order.id},
            current_user=user,
            db=self.db,
        )
        conversation = await self.db.get(SupportConversation, data["id"])
        conversation.unread_user = 1
        conversation.unread_admin = 1
        conversation.unread_merchant = 1
        self.db.add_all(
            [
                SupportMessage(conversation_id=conversation.id, sender_role="user", sender_name="User", content="hello"),
                SupportMessage(conversation_id=conversation.id, sender_role="merchant", sender_name="Merchant", content="reply"),
                SupportMessage(conversation_id=conversation.id, sender_role="admin", sender_name="Admin", content="assist"),
            ]
        )
        await self.db.commit()

        self.assertEqual(3, len(await user_messages(conversation.id, current_user=user, db=self.db)))
        self.assertEqual(3, len(await merchant_messages(conversation.id, school=school, db=self.db)))
        self.assertEqual(3, len(await admin_messages(conversation.id, db=self.db)))
        await self.db.refresh(conversation)
        self.assertEqual((0, 0, 0), (conversation.unread_user, conversation.unread_admin, conversation.unread_merchant))
        closed = await close_conversation(conversation.id, db=self.db)
        self.assertEqual("closed", closed["status"])

    async def test_upload_rejects_unsupported_file_before_storage(self) -> None:
        user = await self.create_user()
        conversation = SupportConversation(id="upload-conversation", user_id=user.user_no, user_name=user.nickname)
        self.db.add(conversation)
        await self.db.commit()
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})
        upload = UploadFile(filename="payload.exe", file=BytesIO(b"not-an-image"))
        with self.assertRaises(HTTPException) as raised:
            await upload_support_image(
                request,
                conversation_id=conversation.id,
                role="user",
                file=upload,
                db=self.db,
                current_user=user,
            )
        self.assertEqual(400, raised.exception.status_code)


class MerchantBusinessE2ETests(DatabaseTestCase):
    async def test_merchant_onboarding_product_purchase_and_support_chain(self) -> None:
        await apply_school(
            SchoolApplicationPayload(
                name="E2E School",
                short_name="E2E",
                city="Chengdu",
                contact_name="Owner",
                merchant_account="e2e-merchant",
                merchant_password="secret123",
            ),
            db=self.db,
        )
        school = await self.db.scalar(select(SchoolSite).where(SchoolSite.merchant_account == "e2e-merchant"))
        await review_school_site(school.id, SchoolReviewPayload(approved=True), db=self.db)

        product_data = await merchant_create_product(
            StudyProductPayload(
                name="E2E Study Plan",
                product_type="package",
                price=Decimal("39.90"),
                original_price=Decimal("59.90"),
                stock=2,
            ),
            school=school,
            db=self.db,
        )
        product_id = product_data["id"]
        await review_product(product_id, StudyProductReviewPayload(approved=True), db=self.db)
        product = await self.db.get(StudyProduct, product_id)
        self.assertTrue(product.status)

        user = await self.create_user(balance=Decimal("100.00"), user_no="U-E2E-MERCHANT")
        fake_redis = FakeOrderRedis()
        with patch("app.services.order_service.redis", fake_redis), patch(
            "app.services.order_service.enqueue_order_payment_timeout"
        ):
            order = await OrderService.create_order(
                self.db,
                user,
                OrderCreateRequest(
                    items=[OrderCreateItem(product_id=product.id)],
                    payment_method="balance",
                    idempotency_key="merchant-e2e-order-001",
                ),
            )
            await OrderService.pay_with_balance(self.db, user, order)

        conversation = await create_order_conversation(
            {"user_id": user.user_no, "order_id": order.id}, current_user=user, db=self.db
        )
        self.assertEqual(school.id, conversation["school_id"])
        self.assertEqual(product.id, conversation["product_id"])
        self.assertEqual("paid", order.payment_status)
        self.assertEqual(1, await self.db.scalar(select(func.count(UserEntitlement.id))))


class LoginGuardAutomationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request(client_ip: str, forwarded: str = "") -> Request:
        headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
        return Request({"type": "http", "method": "POST", "path": "/login", "headers": headers, "client": (client_ip, 1234)})

    async def test_forwarded_ip_is_used_only_for_trusted_proxies(self) -> None:
        with patch("app.core.login_guard.settings.trusted_proxy_ips", "127.0.0.1,10.0.0.0/8"):
            self.assertEqual("203.0.113.8", _client_ip(self.request("127.0.0.1", "203.0.113.8, 10.0.0.2")))
            self.assertEqual("198.51.100.7", _client_ip(self.request("198.51.100.7", "203.0.113.9")))

    async def test_limit_returns_retry_after_and_uses_normalized_identity(self) -> None:
        raw = SimpleNamespace(get=AsyncMock(return_value=5), ttl=AsyncMock(return_value=87))
        with patch("app.core.login_guard.redis", SimpleNamespace(raw=raw)), patch(
            "app.core.login_guard.settings.login_max_attempts", 5
        ):
            with self.assertRaises(HTTPException) as raised:
                await ensure_login_allowed("admin", " Admin@Demo ", self.request("198.51.100.7"))
        self.assertEqual(429, raised.exception.status_code)
        self.assertEqual("87", raised.exception.headers["Retry-After"])
        self.assertEqual(
            _login_key("admin", "admin@demo", "198.51.100.7"),
            _login_key("admin", " Admin@Demo ", "198.51.100.7"),
        )
