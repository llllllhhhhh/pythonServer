from __future__ import annotations

import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.commerce import complete_learning_onboarding
from app.core.database import Base
from app.models import LearningProfile, PreferenceEvent, UserAccount
from app.schemas.api import DecorationPayload
from app.schemas.commerce import LearningOnboardingUpdate


class LearningOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = self.session_factory()
        self.user = UserAccount(
            user_no="U-ONBOARDING",
            phone="15500009999",
            nickname="引导测试用户",
            password_hash="unused",
            role="user",
            status="active",
            is_registered=True,
        )
        self.db.add(self.user)
        await self.db.commit()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await self.engine.dispose()

    async def test_completion_updates_profile_and_is_idempotent_per_version(self) -> None:
        payload = LearningOnboardingUpdate(
            version=2,
            answers={
                "stage": "大三在读",
                "target": "2027 年 12 月",
                "schoolMajorStatus": "只确认了院校",
                "interests": ["计算机", "人工智能"],
            },
        )

        result = await complete_learning_onboarding(payload, user=self.user, db=self.db)
        profile = await self.db.scalar(
            select(LearningProfile).where(LearningProfile.user_id == self.user.id)
        )

        self.assertEqual({"ok": True, "version": 2, "ignored": False}, result)
        self.assertEqual("大三在读", profile.current_stage)
        self.assertEqual("2027 年 12 月", profile.target_exam)
        self.assertEqual("只确认了院校", profile.school_major_status)
        self.assertEqual(["计算机", "人工智能"], profile.interest_tags)
        self.assertEqual(2, profile.onboarding_version)
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertEqual(
            2,
            await self.db.scalar(select(func.count(PreferenceEvent.id))),
        )
        event_user_ids = set(await self.db.scalars(select(PreferenceEvent.user_id)))
        self.assertEqual({str(self.user.id)}, event_user_ids)

        duplicate = await complete_learning_onboarding(
            LearningOnboardingUpdate(
                version=2,
                answers={"stage": "不应覆盖", "interests": ["不应重复"]},
            ),
            user=self.user,
            db=self.db,
        )
        await self.db.refresh(profile)
        self.assertEqual({"ok": True, "version": 2, "ignored": False}, duplicate)
        self.assertEqual("大三在读", profile.current_stage)
        self.assertEqual(2, await self.db.scalar(select(func.count(PreferenceEvent.id))))

        stale = await complete_learning_onboarding(
            LearningOnboardingUpdate(version=1, answers={"stage": "旧数据"}),
            user=self.user,
            db=self.db,
        )
        self.assertEqual({"ok": True, "version": 2, "ignored": True}, stale)

    def test_decoration_payload_keeps_onboarding_configuration(self) -> None:
        payload = DecorationPayload(
            brand={},
            pages=[],
            onboarding={
                "enabled": True,
                "version": 3,
                "steps": [{"key": "stage", "options": ["大一在读"]}],
            },
        )
        dumped = payload.model_dump(mode="json")
        self.assertEqual(3, dumped["onboarding"]["version"])
        self.assertEqual("stage", dumped["onboarding"]["steps"][0]["key"])


if __name__ == "__main__":
    unittest.main()
