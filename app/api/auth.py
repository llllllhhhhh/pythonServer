import hashlib
import secrets
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import generate_token, generate_user_no, hash_password, session_expire_at, verify_and_rehash_password
from app.core.config import settings
from app.core.cache import cache_get_json, cache_set_json
from app.core.database import get_db
from app.core.identity import resolve_user_identity
from app.core.login_guard import clear_login_failures, ensure_login_allowed, record_login_failure
from app.core.security import get_admin_user, get_current_user
from app.core.slider_captcha import consume_slider_ticket, create_slider_challenge, verify_slider_challenge
from app.models import InviteRelation, PointLedger, PointRule, UserAccount, UserSession
from app.services.point_service import PointService
from app.schemas.api import (
    AuthResponse,
    InviteDashboardOut,
    InviteRecordOut,
    LoginPayload,
    RegisterPayload,
    RegisterSubmitResponse,
    SliderCaptchaChallengeOut,
    SliderCaptchaVerifyOut,
    SliderCaptchaVerifyPayload,
    UserOut,
    WechatMiniLoginPayload,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def exchange_wechat_mini_code(code: str) -> str:
    if not settings.wechat_mini_app_id or not settings.wechat_mini_app_secret:
        raise HTTPException(status_code=503, detail="微信小程序登录尚未配置")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.wechat_mini_app_id,
                    "secret": settings.wechat_mini_app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="微信登录服务暂时不可用") from exc
    openid = str(data.get("openid") or "").strip()
    if not openid:
        raise HTTPException(status_code=401, detail="微信登录凭证无效或已过期")
    return openid


async def get_wechat_access_token() -> str:
    cached = await cache_get_json("wechat:mini:access-token")
    if isinstance(cached, dict) and cached.get("token"):
        return str(cached["token"])
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": settings.wechat_mini_app_id,
                    "secret": settings.wechat_mini_app_secret,
                },
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="微信手机号服务暂时不可用") from exc
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=502, detail="微信手机号服务配置无效")
    ttl = max(60, int(data.get("expires_in") or 7200) - 300)
    await cache_set_json("wechat:mini:access-token", {"token": token}, ttl=ttl)
    return token


async def exchange_wechat_phone_code(phone_code: str) -> str:
    access_token = await get_wechat_access_token()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
                params={"access_token": access_token},
                json={"code": phone_code},
            )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="微信手机号服务暂时不可用") from exc
    phone_info = data.get("phone_info") if isinstance(data, dict) else None
    phone = str((phone_info or {}).get("purePhoneNumber") or (phone_info or {}).get("phoneNumber") or "").strip()
    if not phone:
        raise HTTPException(status_code=401, detail="微信手机号凭证无效或已过期")
    if len(phone) > 30:
        raise HTTPException(status_code=400, detail="微信认证手机号格式不正确")
    return phone


def wechat_placeholder_phone(openid: str) -> str:
    return "wx_" + hashlib.sha256(openid.encode("utf-8")).hexdigest()[:27]


def ensure_active_user(user: UserAccount) -> None:
    if user.status == "cancelled":
        raise HTTPException(status_code=403, detail="该账户已注销")
    if user.status != "active" or not user.is_registered:
        raise HTTPException(status_code=403, detail="该账户当前不可登录，请联系平台客服")


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
    identity = await resolve_user_identity(db, user.id)
    output = UserOut.model_validate(user).model_copy(update={"exam_status": identity})
    return AuthResponse(token=token, user=output)


@router.get("/slider-captcha", response_model=SliderCaptchaChallengeOut)
async def slider_captcha(db: AsyncSession = Depends(get_db)):
    return await create_slider_challenge(db)


@router.post("/slider-captcha/verify", response_model=SliderCaptchaVerifyOut)
async def verify_captcha(payload: SliderCaptchaVerifyPayload):
    return await verify_slider_challenge(payload.challenge_id, payload.x, payload.scope)


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
    await db.flush()
    invitation_bound = False
    invite_code = payload.invite_code.strip()
    if invite_code:
        inviter = await db.scalar(
            select(UserAccount).where(
                UserAccount.user_no == invite_code,
                UserAccount.role == "user",
                UserAccount.status == "active",
                UserAccount.is_registered.is_(True),
            )
        )
        if not inviter:
            raise HTTPException(status_code=400, detail="邀请码无效或邀请人账号不可用")
        if inviter.phone == payload.phone:
            raise HTTPException(status_code=400, detail="不能使用自己的邀请码")
        existing_relation = await db.scalar(
            select(InviteRelation).where(InviteRelation.invitee_phone == payload.phone)
        )
        if existing_relation:
            raise HTTPException(status_code=400, detail="该手机号已经绑定过邀请关系")
        device_id = payload.device_id.strip() or "unknown"
        reused_device = None
        if device_id != "unknown":
            reused_device = await db.scalar(
                select(InviteRelation).where(InviteRelation.device_id == device_id)
            )
        db.add(
            InviteRelation(
                inviter_id=inviter.user_no,
                invitee_phone=payload.phone,
                device_id=device_id,
                score_granted=False,
                abnormal=bool(reused_device),
                frozen=False,
            )
        )
        invitation_bound = True
    await db.commit()
    return RegisterSubmitResponse(
        message="注册申请已提交，请等待管理员审核",
        status="pending",
        invitation_bound=invitation_bound,
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginPayload, request: Request, db: AsyncSession = Depends(get_db)):
    login_key = await ensure_login_allowed("user", payload.account, request)
    await consume_slider_ticket(db, payload.slider_ticket, "user")
    user = await db.scalar(
        select(UserAccount).where(
            or_(UserAccount.phone == payload.account, UserAccount.user_no == payload.account),
            UserAccount.role == "user",
        )
    )
    valid_password, upgraded_hash = verify_and_rehash_password(payload.password, user.password_hash) if user else (False, None)
    if not user or not valid_password:
        await record_login_failure(login_key)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    await clear_login_failures(login_key)
    if upgraded_hash:
        user.password_hash = upgraded_hash
    if user.status == "pending" or not user.is_registered:
        raise HTTPException(status_code=403, detail="注册申请尚未审核，请等待管理员通过")
    if user.status == "rejected":
        raise HTTPException(status_code=403, detail="注册申请未通过，请联系管理员")
    if user.status == "cancelled":
        raise HTTPException(status_code=403, detail="该账户已注销")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="该账户已被停用")
    return await build_auth_response(db, user)


@router.post("/wechat-mini/login", response_model=AuthResponse)
async def wechat_mini_login(
    payload: WechatMiniLoginPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    login_key = await ensure_login_allowed("wechat-mini", "mini-program", request)
    try:
        openid = await exchange_wechat_mini_code(payload.code)
        phone = await exchange_wechat_phone_code(payload.phone_code)
    except HTTPException:
        await record_login_failure(login_key)
        raise

    user = await db.scalar(
        select(UserAccount).where(
            UserAccount.wechat_openid == openid,
            UserAccount.role == "user",
        )
    )
    if user:
        ensure_active_user(user)
        phone_owner = await db.scalar(
            select(UserAccount).where(UserAccount.phone == phone, UserAccount.id != user.id)
        )
        if phone_owner:
            raise HTTPException(status_code=409, detail="该手机号已绑定其他账户，请联系平台客服")
        if user.phone.startswith("wx_"):
            user.phone = phone
        if user.nickname in {"微信用户", user.phone} or user.nickname.startswith("wx_"):
            user.nickname = phone
        await clear_login_failures(login_key)
        return await build_auth_response(db, user)

    user = await db.scalar(select(UserAccount).where(UserAccount.phone == phone, UserAccount.role == "user"))
    created = user is None
    if user:
        ensure_active_user(user)
        if user.wechat_openid and user.wechat_openid != openid:
            raise HTTPException(status_code=409, detail="该手机号已绑定其他微信账户，请联系平台客服")
        user.wechat_openid = openid
    else:
        user = UserAccount(
            user_no=generate_user_no(),
            phone=phone,
            wechat_openid=openid,
            nickname=phone,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="user",
            status="active",
            is_registered=True,
            points=0,
        )
        db.add(user)
        await db.flush()

    invite_code = payload.invite_code.strip()
    if created and invite_code:
        inviter = await db.scalar(
            select(UserAccount).where(
                UserAccount.user_no == invite_code,
                UserAccount.role == "user",
                UserAccount.status == "active",
                UserAccount.is_registered.is_(True),
            )
        )
        if inviter:
            device_id = payload.device_id.strip() or "wechat-mini"
            reused_device = await db.scalar(
                select(InviteRelation).where(InviteRelation.device_id == device_id)
            )
            relation = InviteRelation(
                inviter_id=inviter.user_no,
                invitee_phone=phone,
                device_id=device_id,
                score_granted=False,
                abnormal=bool(reused_device),
                frozen=False,
            )
            db.add(relation)
            await db.flush()
            rule = await db.get(PointRule, 1)
            invite_enabled = bool((rule.enabled and rule.invite_enabled) if rule else True)
            invite_score = int(rule.invite_score if rule else 1)
            if not relation.abnormal and invite_enabled and invite_score > 0:
                locked_inviter = await PointService.lock_user(db, inviter.id)
                valid_days = max(1, int(rule.valid_days if rule else 365))
                await PointService.credit_locked(
                    db,
                    locked_inviter,
                    invite_score,
                    biz_type="invite_reward",
                    biz_id=relation.id,
                    biz_no=str(relation.id),
                    idempotency_key=f"invite-reward:{relation.id}",
                    remark=f"邀请微信用户 {user.user_no} 首次登录",
                    expires_at=datetime.now() + timedelta(days=valid_days),
                )
                relation.score_granted = True

    await clear_login_failures(login_key)
    return await build_auth_response(db, user)


@router.post("/admin/login", response_model=AuthResponse)
async def admin_login(payload: LoginPayload, request: Request, db: AsyncSession = Depends(get_db)):
    login_key = await ensure_login_allowed("admin", payload.account, request)
    await consume_slider_ticket(db, payload.slider_ticket, "admin")
    user = await db.scalar(
        select(UserAccount).where(
            or_(UserAccount.phone == payload.account, UserAccount.user_no == payload.account),
            UserAccount.role == "admin",
        )
    )
    valid_password, upgraded_hash = verify_and_rehash_password(payload.password, user.password_hash) if user else (False, None)
    if not user or not valid_password:
        await record_login_failure(login_key)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    await clear_login_failures(login_key)
    if upgraded_hash:
        user.password_hash = upgraded_hash
    if user.status != "active":
        raise HTTPException(status_code=403, detail="管理员账户已被停用")
    return await build_auth_response(db, user)


@router.get("/me", response_model=UserOut)
async def me(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    identity = await resolve_user_identity(db, user.id)
    return UserOut.model_validate(user).model_copy(update={"exam_status": identity})


@router.get("/invite/dashboard", response_model=InviteDashboardOut)
async def invite_dashboard(
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    locked_user = await PointService.lock_user(db, user.id)
    expired = await PointService.expire_due_locked(db, locked_user)
    if expired:
        await db.commit()
    rule = await db.get(PointRule, 1)
    invite_score = rule.invite_score if rule else 1
    exchange_score = rule.exchange_score if rule else 100
    enabled = rule.enabled if rule else True
    relations = list(
        await db.scalars(
            select(InviteRelation)
            .where(InviteRelation.inviter_id == user.user_no)
            .order_by(InviteRelation.created_at.desc())
        )
    )
    phones = [item.invitee_phone for item in relations]
    invitees = {}
    if phones:
        rows = list(await db.scalars(select(UserAccount).where(UserAccount.phone.in_(phones))))
        invitees = {item.phone: item for item in rows}
    records = []
    for relation in relations:
        invitee = invitees.get(relation.invitee_phone)
        records.append(
            InviteRecordOut(
                nickname=invitee.nickname if invitee else "受邀好友",
                phone=relation.invitee_phone,
                status=invitee.status if invitee else "unknown",
                score=invite_score if relation.score_granted else 0,
                score_granted=relation.score_granted,
                created_at=relation.created_at,
            )
        )
    ledger = list(
        await db.scalars(
            select(PointLedger)
            .where(PointLedger.user_id == locked_user.id)
            .order_by(PointLedger.id.desc())
            .limit(100)
        )
    )
    return InviteDashboardOut(
        invite_code=locked_user.user_no,
        invite_payload=f"XTXINVITE:{locked_user.user_no}",
        points=locked_user.points,
        invite_score=invite_score,
        exchange_score=exchange_score,
        enabled=enabled,
        invited_count=len(relations),
        granted_count=sum(1 for item in relations if item.score_granted),
        records=records,
        ledger=ledger,
    )


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
