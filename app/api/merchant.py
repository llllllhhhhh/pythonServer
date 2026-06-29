from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import generate_token, hash_password, session_expire_at, verify_password
from app.core.database import get_db
from app.models import SchoolMerchantSession, SchoolSite
from app.schemas.api import (
    LoginPayload,
    MerchantLoginResponse,
    SchoolApplicationPayload,
    SchoolApplicationResponse,
    SchoolSiteOut,
)

router = APIRouter(prefix="/merchant", tags=["merchant"])


def build_school_out(item: SchoolSite) -> SchoolSiteOut:
    return SchoolSiteOut.model_validate(item).model_copy(
        update={"has_merchant_password": bool(item.merchant_password_hash)}
    )


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def get_current_school(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> SchoolSite:
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录商户端")
    session = await db.get(SchoolMerchantSession, token)
    if not session or session.expires_at < datetime.now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    school = await db.get(SchoolSite, session.school_id)
    if not school or school.review_status != "approved" or not school.status:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="学校未审核通过或已下架")
    session.last_seen_at = datetime.now()
    await db.commit()
    return school


@router.post("/apply", response_model=SchoolApplicationResponse, status_code=status.HTTP_201_CREATED)
async def apply_school(payload: SchoolApplicationPayload, db: AsyncSession = Depends(get_db)):
    exists = await db.scalar(
        select(SchoolSite).where(
            or_(
                SchoolSite.name == payload.name,
                SchoolSite.merchant_account == payload.merchant_account,
            )
        )
    )
    if exists:
        raise HTTPException(status_code=400, detail="该学校或商户账号已提交过入驻申请")
    item = SchoolSite(
        name=payload.name,
        short_name=payload.short_name,
        city=payload.city,
        district=payload.district,
        logo=payload.logo,
        description=payload.description or f"联系人：{payload.contact_name}",
        status=False,
        current=False,
        review_status="pending",
        reject_reason="",
        merchant_account=payload.merchant_account,
        merchant_password_hash=hash_password(payload.merchant_password),
    )
    db.add(item)
    await db.commit()
    return SchoolApplicationResponse(message="入驻申请已提交，请等待平台审核", status="pending")


@router.post("/login", response_model=MerchantLoginResponse)
async def merchant_login(payload: LoginPayload, db: AsyncSession = Depends(get_db)):
    school = await db.scalar(select(SchoolSite).where(SchoolSite.merchant_account == payload.account))
    if not school or not school.merchant_password_hash or not verify_password(payload.password, school.merchant_password_hash):
        raise HTTPException(status_code=401, detail="商户账号或密码错误")
    if school.review_status == "pending":
        raise HTTPException(status_code=403, detail="入驻申请正在审核中")
    if school.review_status == "rejected":
        raise HTTPException(status_code=403, detail=school.reject_reason or "入驻申请未通过")
    if not school.status:
        raise HTTPException(status_code=403, detail="学校已下架，暂不能登录商户端")
    await db.execute(delete(SchoolMerchantSession).where(SchoolMerchantSession.school_id == school.id))
    token = generate_token()
    db.add(
        SchoolMerchantSession(
            token=token,
            school_id=school.id,
            expires_at=session_expire_at(),
            last_seen_at=datetime.now(),
        )
    )
    await db.commit()
    await db.refresh(school)
    return MerchantLoginResponse(token=token, school=build_school_out(school))


@router.get("/me", response_model=SchoolSiteOut)
async def merchant_me(school: SchoolSite = Depends(get_current_school)):
    return build_school_out(school)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def merchant_logout(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = extract_bearer_token(authorization)
    if token:
        await db.execute(delete(SchoolMerchantSession).where(SchoolMerchantSession.token == token))
        await db.commit()
