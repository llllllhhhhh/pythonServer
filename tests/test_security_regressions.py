from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta

from pydantic import ValidationError
from fastapi import HTTPException
from starlette.requests import Request

from app.api.public import _authorize_asset, build_thumbnail
from app.api.support import authorize_support_websocket
from app.api.travel_orders import _travel_order_owned_by
from app.core.asset_access import (
    asset_api_url,
    create_asset_access_token,
    extract_asset_reference,
    is_public_asset_source,
    sign_asset_url,
    verify_asset_access_token,
)
from app.core.auth import hash_password, verify_and_rehash_password, verify_password
from app.core.config import Settings
from app.models import (
    SchoolMerchantSession,
    SchoolSite,
    SupportConversation,
    TravelOrder,
    UserAccount,
    UserSession,
    UploadedAsset,
)
from app.schemas.api import AnnouncementCreate


class FakeAsyncSession:
    def __init__(self, values: dict[tuple[type, object], object]):
        self.values = values
        self.commits = 0

    async def get(self, model: type, key: object):
        return self.values.get((model, key))

    async def commit(self) -> None:
        self.commits += 1


def user_account(user_id: int, user_no: str, phone: str, role: str = "user") -> UserAccount:
    return UserAccount(
        id=user_id,
        user_no=user_no,
        phone=phone,
        nickname=user_no,
        password_hash="unused",
        role=role,
        status="active",
        is_registered=True,
    )


class TravelOrderOwnershipTests(unittest.TestCase):
    def test_same_phone_suffix_does_not_grant_ownership(self) -> None:
        owner = user_account(1, "U001", "15533428586")
        attacker = user_account(2, "U002", "18800008586")
        order = TravelOrder(id=10, user_id=owner.id, user_no=owner.user_no, phone=owner.phone)

        self.assertTrue(_travel_order_owned_by(order, owner))
        self.assertFalse(_travel_order_owned_by(order, attacker))

    def test_user_number_fallback_does_not_override_database_owner(self) -> None:
        current_user = user_account(2, "U-SHARED", "18800000001")
        order = TravelOrder(id=11, user_id=1, user_no="U-SHARED", phone=current_user.phone)

        self.assertFalse(_travel_order_owned_by(order, current_user))


class SupportWebSocketAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_requires_valid_session_and_matching_conversation(self) -> None:
        user = user_account(1, "U001", "15500000001")
        session = UserSession(
            token="user-token",
            user_id=user.id,
            role="user",
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        conversation = SupportConversation(id="conversation-1", user_id=user.user_no)
        db = FakeAsyncSession(
            {
                (UserSession, session.token): session,
                (UserAccount, user.id): user,
            }
        )

        self.assertTrue(await authorize_support_websocket(db, conversation, "user", session.token))
        self.assertFalse(await authorize_support_websocket(db, conversation, "user", ""))

        other_conversation = SupportConversation(id="conversation-2", user_id="U999")
        self.assertFalse(await authorize_support_websocket(db, other_conversation, "user", session.token))

    async def test_expired_user_session_is_rejected(self) -> None:
        user = user_account(1, "U001", "15500000001")
        session = UserSession(
            token="expired-token",
            user_id=user.id,
            role="user",
            expires_at=datetime.now() - timedelta(seconds=1),
        )
        conversation = SupportConversation(id="conversation-1", user_id=user.user_no)
        db = FakeAsyncSession(
            {
                (UserSession, session.token): session,
                (UserAccount, user.id): user,
            }
        )

        self.assertFalse(await authorize_support_websocket(db, conversation, "user", session.token))

    async def test_admin_requires_an_active_admin_session(self) -> None:
        admin = user_account(9, "ADMIN001", "15500000009", role="admin")
        session = UserSession(
            token="admin-token",
            user_id=admin.id,
            role="admin",
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        conversation = SupportConversation(id="conversation-1", user_id="U001")
        db = FakeAsyncSession(
            {
                (UserSession, session.token): session,
                (UserAccount, admin.id): admin,
            }
        )

        self.assertTrue(await authorize_support_websocket(db, conversation, "admin", session.token))
        self.assertFalse(await authorize_support_websocket(db, conversation, "admin", "xuetuxing-dev-key"))

    async def test_merchant_must_match_an_approved_school(self) -> None:
        school = SchoolSite(id=5, name="Test School", review_status="approved", status=True)
        session = SchoolMerchantSession(
            token="merchant-token",
            school_id=school.id,
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        conversation = SupportConversation(id="conversation-1", user_id="U001", school_id=school.id)
        db = FakeAsyncSession(
            {
                (SchoolMerchantSession, session.token): session,
                (SchoolSite, school.id): school,
            }
        )

        self.assertTrue(await authorize_support_websocket(db, conversation, "merchant", session.token))
        conversation.school_id = 99
        self.assertFalse(await authorize_support_websocket(db, conversation, "merchant", session.token))


class AssetAccessTokenTests(unittest.TestCase):
    def test_asset_token_is_scoped_and_expires(self) -> None:
        token = create_asset_access_token(42, "file", expires_at=2_000)

        self.assertTrue(verify_asset_access_token(42, "file", token, now=1_999))
        self.assertTrue(verify_asset_access_token(42, "thumb", token, now=1_999))
        self.assertFalse(verify_asset_access_token(43, "file", token, now=1_999))
        self.assertFalse(verify_asset_access_token(42, "file", token, now=2_001))

    def test_asset_url_is_signed_without_changing_reference(self) -> None:
        raw = asset_api_url(8)
        signed = sign_asset_url(raw)

        self.assertEqual((8, "file"), extract_asset_reference(signed))
        self.assertIn("asset_token=", signed)

    def test_only_explicit_sources_are_public(self) -> None:
        self.assertTrue(is_public_asset_source("travel_review"))
        self.assertTrue(is_public_asset_source("common"))
        self.assertFalse(is_public_asset_source("support"))
        self.assertFalse(is_public_asset_source("support_thumb"))
        self.assertFalse(is_public_asset_source("contract_signature"))
        self.assertFalse(is_public_asset_source("admission_notice"))
        self.assertFalse(is_public_asset_source("unknown"))

    def test_thumbnail_builder_executes_image_conversion(self) -> None:
        from io import BytesIO

        from PIL import Image

        source = BytesIO()
        Image.new("RGB", (800, 400), "red").save(source, format="PNG")
        thumbnail = build_thumbnail(source.getvalue(), max_width=200)

        self.assertIsNotNone(thumbnail)
        with Image.open(BytesIO(thumbnail or b"")) as image:
            self.assertEqual((200, 100), image.size)


class AssetAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request() -> Request:
        return Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    async def test_public_asset_is_anonymous(self) -> None:
        asset = UploadedAsset(id=1, source="travel_review")
        allowed_as_public = await _authorize_asset(
            asset,
            self.request(),
            FakeAsyncSession({}),
            "file",
            "",
        )
        self.assertTrue(allowed_as_public)

    async def test_private_asset_requires_authorization(self) -> None:
        asset = UploadedAsset(id=2, source="contract_signature", user_id=7)
        with self.assertRaises(HTTPException) as context:
            await _authorize_asset(asset, self.request(), FakeAsyncSession({}), "file", "")
        self.assertEqual(401, context.exception.status_code)

    async def test_private_asset_accepts_short_lived_signed_url(self) -> None:
        asset = UploadedAsset(id=3, source="support", conversation_id="conversation-1")
        token = create_asset_access_token(asset.id, "file")
        allowed_as_public = await _authorize_asset(
            asset,
            self.request(),
            FakeAsyncSession({}),
            "file",
            token,
        )
        self.assertFalse(allowed_as_public)


class PasswordHashTests(unittest.TestCase):
    def test_new_passwords_use_argon2id(self) -> None:
        encoded = hash_password("correct horse battery staple")

        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))

    def test_legacy_sha256_is_upgraded_after_successful_verification(self) -> None:
        salt = "0123456789abcdef0123456789abcdef"
        digest = hashlib.sha256(f"{salt}:legacy-password".encode("utf-8")).hexdigest()
        legacy = f"{salt}${digest}"

        valid, upgraded = verify_and_rehash_password("legacy-password", legacy)

        self.assertTrue(valid)
        self.assertIsNotNone(upgraded)
        self.assertTrue(str(upgraded).startswith("$argon2id$"))
        self.assertTrue(verify_password("legacy-password", str(upgraded)))


class ProductionConfigurationTests(unittest.TestCase):
    def production_settings(self, **overrides) -> Settings:
        values = {
            "environment": "production",
            "app_secret_key": "a-production-secret-key-with-more-than-32-characters",
            "cors_origins": "https://goxuetuxing.com",
            "mysql_password": "a-non-default-database-password",
            "redis_enabled": True,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_secure_production_configuration_is_accepted(self) -> None:
        settings = self.production_settings()
        self.assertEqual("production", settings.environment)

    def test_development_secret_is_rejected_in_production(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(app_secret_key="development-only-change-me-32-bytes")

    def test_wildcard_cors_is_rejected_in_production(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(cors_origins="*")

    def test_insecure_cors_origin_is_rejected_in_production(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(cors_origins="http://goxuetuxing.com")

    def test_default_database_password_is_rejected_in_production(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(mysql_password="xuetuxing123")

    def test_production_environment_is_normalized_before_validation(self) -> None:
        settings = self.production_settings(environment=" Production ")
        self.assertEqual("production", settings.environment)
        with self.assertRaises(ValidationError):
            self.production_settings(
                environment="Production",
                app_secret_key="development-only-change-me-32-bytes",
            )

    def test_empty_cors_is_rejected_in_production(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(cors_origins="  ")


class AnnouncementValidationTests(unittest.TestCase):
    def test_end_time_cannot_be_earlier_than_start_time(self) -> None:
        start_at = datetime(2026, 8, 17, 12, 0, 0)
        with self.assertRaises(ValidationError):
            AnnouncementCreate(
                title="Maintenance notice",
                content="Scheduled maintenance",
                start_at=start_at,
                end_at=start_at - timedelta(minutes=1),
            )


if __name__ == "__main__":
    unittest.main()
