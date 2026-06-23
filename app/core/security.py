from __future__ import annotations

from datetime import datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models import UserAccount, UserSession


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def _load_session_user(
    db: AsyncSession,
    authorization: str | None,
    expected_role: str | None = None,
) -> UserAccount | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None
    session = await db.get(UserSession, token)
    if not session or session.expires_at < datetime.now():
        return None
    if expected_role and session.role != expected_role:
        return None
    user = await db.get(UserAccount, session.user_id)
    if not user or user.status != "active":
        return None
    session.last_seen_at = datetime.now()
    await db.commit()
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> UserAccount:
    user = await _load_session_user(db, authorization)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


async def get_optional_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> UserAccount | None:
    return await _load_session_user(db, authorization)


async def require_admin(
    x_admin_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> UserAccount | None:
    if x_admin_key == settings.admin_api_key:
        return None
    user = await _load_session_user(db, authorization, expected_role="admin")
    if user:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员身份无效")


async def get_admin_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> UserAccount:
    user = await _load_session_user(db, authorization, expected_role="admin")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录管理端账号")
    return user


async def get_user_by_phone(db: AsyncSession, phone: str) -> UserAccount | None:
    return await db.scalar(select(UserAccount).where(UserAccount.phone == phone))
