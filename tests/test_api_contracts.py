from __future__ import annotations

import unittest
from datetime import datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.security import get_current_user, get_optional_user, require_admin
from app.main import app
from app.models import InviteRelation, PointLedger, SupportConversation, UserAccount


class ApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = self.session_factory()
        self.user = UserAccount(
            user_no="U-API-CONTRACT",
            phone="15500000009",
            nickname="API Contract User",
            password_hash="unused",
            role="user",
            status="active",
            is_registered=True,
        )
        self.admin = UserAccount(
            user_no="ADMIN-API-CONTRACT",
            phone="15500000010",
            nickname="API Contract Admin",
            password_hash="unused",
            role="admin",
            status="active",
            is_registered=True,
        )
        self.db.add_all([self.user, self.admin])
        await self.db.commit()

        async def override_db():
            yield self.db

        async def override_admin():
            return self.admin

        async def override_user():
            return self.user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_admin] = override_admin
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_optional_user] = override_user
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.db.close()
        await self.engine.dispose()

    async def test_health_and_announcement_http_contract(self) -> None:
        health = await self.client.get("/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual("ok", health.json()["status"])

        invalid = await self.client.post(
            "/api/v1/admin/announcements",
            json={
                "title": "Invalid window",
                "content": "content",
                "start_at": "2026-08-18T10:00:00",
                "end_at": "2026-08-17T10:00:00",
            },
        )
        self.assertEqual(422, invalid.status_code)

        created = await self.client.post(
            "/api/v1/admin/announcements",
            json={"title": "HTTP announcement", "content": "contract", "status": True},
        )
        self.assertEqual(201, created.status_code)
        self.assertTrue(created.json()["status"])
        rows = await self.client.get("/api/v1/admin/announcements")
        self.assertEqual(1, len(rows.json()))

    async def test_invite_binding_updates_both_dashboards_once(self) -> None:
        inviter = UserAccount(
            user_no="U-INVITER",
            phone="15500000011",
            nickname="Inviter",
            password_hash="unused",
            role="user",
            status="active",
            is_registered=True,
            points=0,
        )
        self.db.add(inviter)
        await self.db.commit()

        payload = {"invite_code": inviter.user_no, "device_id": "invite-test-device"}
        first = await self.client.post("/api/v1/auth/invite/bind", json=payload)
        self.assertEqual(200, first.status_code)
        self.assertTrue(first.json()["rewarded"])

        invitee_dashboard = await self.client.get("/api/v1/auth/invite/dashboard")
        self.assertEqual(inviter.user_no, invitee_dashboard.json()["bound_inviter_code"])
        self.assertEqual(0, invitee_dashboard.json()["invited_count"])

        async def override_inviter():
            return inviter

        app.dependency_overrides[get_current_user] = override_inviter
        inviter_dashboard = await self.client.get("/api/v1/auth/invite/dashboard")
        self.assertEqual(1, inviter_dashboard.json()["invited_count"])
        self.assertEqual(1, inviter_dashboard.json()["granted_count"])
        self.assertEqual(1, inviter_dashboard.json()["points"])

        app.dependency_overrides[get_current_user] = lambda: self.user
        second = await self.client.post("/api/v1/auth/invite/bind", json=payload)
        self.assertEqual(200, second.status_code)
        self.assertEqual(1, len(list(await self.db.scalars(select(InviteRelation).where(InviteRelation.inviter_id == inviter.user_no)))))
        self.assertEqual(1, len(list(await self.db.scalars(select(PointLedger).where(PointLedger.biz_type == "invite_reward")))))

        self_bind = await self.client.post("/api/v1/auth/invite/bind", json={"invite_code": self.user.user_no})
        self.assertEqual(400, self_bind.status_code)

    async def test_support_rest_and_upload_validation_http_contract(self) -> None:
        conversation = SupportConversation(
            id="api-contract-conversation",
            user_id=self.user.user_no,
            user_name=self.user.nickname,
            unread_user=2,
        )
        self.db.add(conversation)
        await self.db.commit()

        messages = await self.client.get(f"/api/v1/support/conversations/{conversation.id}/messages")
        self.assertEqual(200, messages.status_code)
        self.assertEqual([], messages.json())
        await self.db.refresh(conversation)
        self.assertEqual(0, conversation.unread_user)

        rejected = await self.client.post(
            "/api/v1/support/upload",
            data={"conversation_id": conversation.id, "role": "user"},
            files={"file": ("payload.exe", b"not-image", "application/octet-stream")},
        )
        self.assertEqual(400, rejected.status_code)
        self.assertIn("jpg/png/gif/webp", rejected.json()["detail"])

    async def test_support_user_cannot_read_another_users_conversation(self) -> None:
        conversation = SupportConversation(
            id="api-contract-forbidden",
            user_id="U-SOMEONE-ELSE",
            user_name="Someone Else",
        )
        self.db.add(conversation)
        await self.db.commit()

        response = await self.client.get(f"/api/v1/support/conversations/{conversation.id}/messages")
        self.assertEqual(404, response.status_code)

    async def test_admin_can_configure_vip_plans_used_by_public_checkout(self) -> None:
        payload = {
            "plans": [
                {
                    "id": 21,
                    "name": "季度会员",
                    "vip_type": "季度 VIP",
                    "price": 29.9,
                    "duration_days": 90,
                    "note": "季度套餐",
                    "badge": "推荐",
                    "benefits": ["专属文章", "会员身份标识"],
                    "enabled": True,
                    "sort_order": 10,
                },
                {
                    "id": 22,
                    "name": "内部套餐",
                    "vip_type": "内部 VIP",
                    "price": 1,
                    "duration_days": 1,
                    "note": "不上架",
                    "badge": "",
                    "benefits": ["内部权益"],
                    "enabled": False,
                    "sort_order": 20,
                },
            ]
        }
        saved = await self.client.put("/api/v1/admin/vip/plans", json=payload)
        self.assertEqual(200, saved.status_code)
        self.assertEqual(2, len(saved.json()))

        public = await self.client.get("/api/v1/public/vip/plans")
        self.assertEqual(200, public.status_code)
        self.assertEqual([21], [item["id"] for item in public.json()])
        self.assertEqual(["专属文章", "会员身份标识"], public.json()[0]["benefits"])

        order = await self.client.post(
            "/api/v1/commerce/vip-orders",
            json={"plan_id": 21, "payment_method": "wechat", "idempotency_key": "vip-plan-contract-21"},
        )
        self.assertEqual(201, order.status_code)
        self.assertEqual("29.90", order.json()["payable_amount"])
