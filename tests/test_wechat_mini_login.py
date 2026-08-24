from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.auth import (
    ensure_active_user,
    exchange_wechat_mini_code,
    exchange_wechat_phone_code,
    wechat_placeholder_phone,
)
from app.models import UserAccount
from app.schemas.api import WechatMiniLoginPayload


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeClient:
    def __init__(self, payload: dict):
        self.response = FakeResponse(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, *_args, **_kwargs):
        return self.response

    async def post(self, *_args, **_kwargs):
        return self.response


class WechatMiniLoginTests(unittest.IsolatedAsyncioTestCase):
    def test_placeholder_phone_is_stable_and_fits_database_column(self) -> None:
        first = wechat_placeholder_phone("openid-for-test")
        second = wechat_placeholder_phone("openid-for-test")
        self.assertEqual(first, second)
        self.assertEqual(30, len(first))
        self.assertTrue(first.startswith("wx_"))

    def test_payload_accepts_invitation_context(self) -> None:
        payload = WechatMiniLoginPayload(
            code="temporary-code",
            phone_code="temporary-phone-code",
            invite_code="U001",
            device_id="device-1",
        )
        self.assertEqual("U001", payload.invite_code)
        self.assertEqual("temporary-phone-code", payload.phone_code)

    def test_inactive_user_cannot_login(self) -> None:
        user = UserAccount(
            user_no="U001",
            phone="wx_test",
            nickname="微信用户",
            password_hash="unused",
            role="user",
            status="disabled",
            is_registered=True,
        )
        with self.assertRaises(HTTPException):
            ensure_active_user(user)

    async def test_exchange_returns_openid_without_exposing_session_key(self) -> None:
        fake_settings = type("Settings", (), {
            "wechat_mini_app_id": "test-app-id",
            "wechat_mini_app_secret": "test-secret",
        })()
        with patch("app.api.auth.settings", fake_settings), patch(
            "app.api.auth.httpx.AsyncClient",
            return_value=FakeClient({"openid": "openid-123", "session_key": "private"}),
        ):
            self.assertEqual("openid-123", await exchange_wechat_mini_code("temporary-code"))

    async def test_exchange_rejects_invalid_wechat_response(self) -> None:
        fake_settings = type("Settings", (), {
            "wechat_mini_app_id": "test-app-id",
            "wechat_mini_app_secret": "test-secret",
        })()
        with patch("app.api.auth.settings", fake_settings), patch(
            "app.api.auth.httpx.AsyncClient",
            return_value=FakeClient({"errcode": 40029, "errmsg": "invalid code"}),
        ):
            with self.assertRaises(HTTPException) as caught:
                await exchange_wechat_mini_code("expired-code")
        self.assertEqual(401, caught.exception.status_code)

    async def test_exchange_phone_code_returns_verified_phone(self) -> None:
        with patch(
            "app.api.auth.get_wechat_access_token",
            new=AsyncMock(return_value="access-token"),
        ), patch(
            "app.api.auth.httpx.AsyncClient",
            return_value=FakeClient({"phone_info": {"purePhoneNumber": "13800138000"}}),
        ):
            self.assertEqual("13800138000", await exchange_wechat_phone_code("phone-code"))

    async def test_exchange_phone_code_rejects_invalid_response(self) -> None:
        with patch(
            "app.api.auth.get_wechat_access_token",
            new=AsyncMock(return_value="access-token"),
        ), patch(
            "app.api.auth.httpx.AsyncClient",
            return_value=FakeClient({"errcode": 40029, "errmsg": "invalid code"}),
        ):
            with self.assertRaises(HTTPException) as caught:
                await exchange_wechat_phone_code("expired-phone-code")
        self.assertEqual(401, caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
