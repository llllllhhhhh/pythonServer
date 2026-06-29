from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GraduationCertification, UserEntitlement


async def resolve_user_identities(db: AsyncSession, user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    identities = {user_id: "学员" for user_id in user_ids}
    active_learners = set(
        await db.scalars(
            select(UserEntitlement.user_id).where(
                UserEntitlement.user_id.in_(user_ids),
                UserEntitlement.status == "active",
                or_(UserEntitlement.expires_at.is_(None), UserEntitlement.expires_at > datetime.now()),
            )
        )
    )
    for user_id in active_learners:
        identities[user_id] = "备考学员"

    verified_users = set(
        await db.scalars(
            select(GraduationCertification.user_id).where(
                GraduationCertification.user_id.in_(user_ids),
                GraduationCertification.status == "approved",
            )
        )
    )
    for user_id in verified_users:
        identities[user_id] = "已录取认证"
    return identities


async def resolve_user_identity(db: AsyncSession, user_id: int) -> str:
    return (await resolve_user_identities(db, [user_id])).get(user_id, "学员")
