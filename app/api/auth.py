from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import generate_token, generate_user_no, hash_password, session_expire_at, verify_password
from app.core.database import get_db
from app.core.security import get_admin_user, get_current_user
from app.models import UserAccount, UserSession
from app.schemas.api import (
    AuthResponse,
    LoginPayload,
    RegisterPayload,
    RegisterSubmitResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def build_auth_response(db: AsyncSession, user: UserAccount) -> AuthResponse:
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    token = generate_token()
    session = UserSession(
        token=token,
        user_id=user.id,
        role=user.role,
        expires_at=session_expire_at(),
        last_seen_at=datetime.now(),
    )
    user.last_login_at = datetime.now()
    db.add(session)
    await db.commit()
    await db.refresh(user)
    return AuthResponse(token=token, user=UserOut.model_validate(user))


@router.post("/register", response_model=RegisterSubmitResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterPayload, db: AsyncSession = Depends(get_db)):
    exists = await db.scalar(select(UserAccount).where(UserAccount.phone == payload.phone))
    if exists:
        raise HTTPException(status_code=400, detail="该手机号已提交注册或已存在账户")
    user = UserAccount(
        user_no=generate_user_no(),
        phone=payload.phone,
        nickname=payload.nickname or "小徒同学",
        password_hash=hash_password(payload.password),
        role="user",
        status="pending",
        is_registered=False,
        points=0,
    )
    db.add(user)
    await db.commit()
    return RegisterSubmitResponse(message="注册申请已提交，请等待管理员审核", status="pending")


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginPayload, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(
        select(UserAccount).where(
            or_(UserAccount.phone == payload.account, UserAccount.user_no == payload.account),
            UserAccount.role == "user",
        )
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if user.status == "pending" or not user.is_registered:
        raise HTTPException(status_code=403, detail="注册申请尚未审核，请等待管理员通过")
    if user.status == "rejected":
        raise HTTPException(status_code=403, detail="注册申请未通过，请联系管理员")
    if user.status == "cancelled":
        raise HTTPException(status_code=403, detail="该账户已注销")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="该账户已被停用")
    return await build_auth_response(db, user)


@router.post("/admin/login", response_model=AuthResponse)
async def admin_login(payload: LoginPayload, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(
        select(UserAccount).where(
            or_(UserAccount.phone == payload.account, UserAccount.user_no == payload.account),
            UserAccount.role == "admin",
        )
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="管理员账户已被停用")
    return await build_auth_response(db, user)


@router.get("/me", response_model=UserOut)
async def me(user: UserAccount = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.get("/admin/me", response_model=UserOut)
async def admin_me(user: UserAccount = Depends(get_admin_user)):
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    await db.commit()


@router.get("/stats")
async def auth_stats(db: AsyncSession = Depends(get_db)):
    total_users = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "user")) or 0
    pending_users = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.status == "pending")
    ) or 0
    return {"total_users": total_users, "pending_users": pending_users}
