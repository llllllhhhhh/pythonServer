import logging
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import PUBLIC_CONFIG_KEY
from app.api.travel_orders import attach_travel_order_details, normalize_fulfillment_status
from app.core.auth import hash_password
from app.core.asset_access import asset_api_url
from app.core.cache import RedisUnavailableError, cache_delete, redis
from app.core.config import settings
from app.core.contract_settings import get_contract_template, save_contract_template
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.core.identity import resolve_user_identities, resolve_user_identity
from app.core.vip import is_vip_active
from app.core.order_status import CommerceOrderStatus, CommercePaymentStatus, TravelContractStatus, TravelFulfillmentStatus, TravelOrderStatus
from app.core.security import require_admin
from app.core.slider_captcha import get_slider_captcha_settings, save_slider_captcha_settings
from app.core.snowflake import SnowflakeGenerator
from app.core.travel_match_settings import get_travel_match_settings, save_travel_match_settings
from app.core.upload_settings import get_upload_setting, save_upload_setting
from app.core.storage import save_upload_bytes, sniff_image_type
from app.models import (
    CommerceOrder,
    CommerceOrderItem,
    ContentArticle,
    DecorationConfig,
    GraduationCertification,
    InviteRelation,
    PlatformAnnouncement,
    PointRule,
    PreferenceEvent,
    SchoolSite,
    SupportConversation,
    TravelOrder,
    TravelOrderAuditLog,
    TravelRoute,
    TravelVerificationRecord,
    UploadedAsset,
    UserAccount,
    UserEntitlement,
    UserSession,
    WalletTransaction,
)
from app.schemas.api import (
    AdminUserRow,
    AdminUserDetail,
    AdminUsersResponse,
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    DecorationPayload,
    DecorationResponse,
    InviteOut,
    GraduationReviewPayload,
    ContractReviewPayload,
    ContractTemplateOut,
    ContractTemplatePayload,
    OrderOut,
    PasswordResetPayload,
    PointRuleOut,
    PointRulePayload,
    PointsAdjustPayload,
    RegistrationReviewPayload,
    RegistrationRow,
    RouteCreate,
    RouteOut,
    RouteReviewPayload,
    RouteUpdate,
    MerchantPasswordPayload,
    SchoolSiteCreate,
    SchoolSiteOut,
    SchoolSiteUpdate,
    SchoolReviewPayload,
    SliderCaptchaSettingsOut,
    SliderCaptchaSettingsPayload,
    StatusUpdatePayload,
    VipUpdatePayload,
    UploadedAssetOut,
    UploadSettingOut,
    UploadSettingPayload,
    UserSummaryOut,
    WalletAdjustPayload,
    TravelOrderCancelPayload,
    TravelOrderExceptionPayload,
    TravelPickupSchedulePayload,
)
from app.services.point_service import PointService
from app.services.order_service import OrderService
from app.services.travel_order_service import (
    add_travel_audit,
    add_verification_record,
    cancel_travel_order,
    qr_expiry,
)
from app.services.wechat_pay_service import (
    create_refund as create_wechat_refund,
    query_refund as query_wechat_refund,
    validate_refund_result,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

logger = logging.getLogger(__name__)


def build_user_row(
    user: UserAccount,
    user_conversations: list[SupportConversation],
    graduation: GraduationCertification | None = None,
    exam_status: str | None = None,
) -> AdminUserRow:
    return AdminUserRow(
        id=user.id,
        user_no=user.user_no,
        phone=user.phone,
        nickname=user.nickname,
        role=user.role,
        status=user.status,
        is_registered=user.is_registered,
        points=user.points,
        balance=Decimal(user.balance or 0),
        exam_status=exam_status or user.exam_status,
        vip_enabled=user.vip_enabled,
        vip_active=is_vip_active(user),
        vip_type=user.vip_type,
        vip_started_at=user.vip_started_at,
        vip_expires_at=user.vip_expires_at,
        vip_remark=user.vip_remark,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        conversation_count=len(user_conversations),
        open_conversation=any(item.status == "open" for item in user_conversations),
        graduation_status=graduation.status if graduation else "not_submitted",
    )


def build_school_out(item: SchoolSite) -> SchoolSiteOut:
    return SchoolSiteOut.model_validate(item).model_copy(
        update={"has_merchant_password": bool(item.merchant_password_hash)}
    )


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    routes = await db.scalar(select(func.count(TravelRoute.id)))
    pending_orders = await db.scalar(select(func.count(TravelOrder.id)).where(TravelOrder.status == int(TravelOrderStatus.CREATED)))
    abnormal_invites = await db.scalar(select(func.count(InviteRelation.id)).where(InviteRelation.abnormal.is_(True)))
    total_users = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "user"))
    return {
        "routes": routes or 0,
        "pending_orders": pending_orders or 0,
        "abnormal_invites": abnormal_invites or 0,
        "users": total_users or 0,
    }


@router.get("/decoration/draft", response_model=DecorationResponse)
async def get_draft(db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(DecorationConfig).where(DecorationConfig.status == "draft").order_by(DecorationConfig.version.desc()))
    if not item:
        return DecorationResponse(status="draft", content={})
    return DecorationResponse(id=item.id, version=item.version, status=item.status, content=item.content, published_at=item.published_at)


@router.put("/decoration/draft", response_model=DecorationResponse)
async def save_draft(payload: DecorationPayload, db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(DecorationConfig).where(DecorationConfig.status == "draft").order_by(DecorationConfig.version.desc()))
    if item:
        item.content = payload.model_dump(mode="json")
        item.version += 1
    else:
        item = DecorationConfig(status="draft", version=1, content=payload.model_dump(mode="json"))
        db.add(item)
    await db.commit()
    await db.refresh(item)
    return DecorationResponse(id=item.id, version=item.version, status=item.status, content=item.content)


@router.post("/decoration/publish", response_model=DecorationResponse)
async def publish_decoration(payload: DecorationPayload, db: AsyncSession = Depends(get_db)):
    last_version = await db.scalar(select(func.max(DecorationConfig.version))) or 0
    item = DecorationConfig(
        status="published",
        version=last_version + 1,
        content=payload.model_dump(mode="json"),
        published_at=datetime.now(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await cache_delete(PUBLIC_CONFIG_KEY)
    return DecorationResponse(
        id=item.id,
        version=item.version,
        status=item.status,
        content=item.content,
        published_at=item.published_at,
    )


@router.get("/upload/settings", response_model=UploadSettingOut)
async def upload_settings(db: AsyncSession = Depends(get_db)):
    return await get_upload_setting(db)


@router.put("/upload/settings", response_model=UploadSettingOut)
async def update_upload_settings(payload: UploadSettingPayload, db: AsyncSession = Depends(get_db)):
    return await save_upload_setting(db, payload.max_image_mb)


@router.get("/slider-captcha/settings", response_model=SliderCaptchaSettingsOut)
async def admin_slider_captcha_settings(db: AsyncSession = Depends(get_db)):
    return await get_slider_captcha_settings(db)


@router.put("/slider-captcha/settings", response_model=SliderCaptchaSettingsOut)
async def update_admin_slider_captcha_settings(
    payload: SliderCaptchaSettingsPayload,
    db: AsyncSession = Depends(get_db),
):
    return await save_slider_captcha_settings(db, payload.model_dump())


@router.get("/contract-template", response_model=ContractTemplateOut)
async def admin_contract_template(db: AsyncSession = Depends(get_db)):
    return await get_contract_template(db)


@router.put("/contract-template", response_model=ContractTemplateOut)
async def update_admin_contract_template(payload: ContractTemplatePayload, db: AsyncSession = Depends(get_db)):
    return await save_contract_template(db, payload.model_dump())


@router.get("/travel-match/settings")
async def admin_travel_match_settings(db: AsyncSession = Depends(get_db)):
    return await get_travel_match_settings(db)


@router.put("/travel-match/settings")
async def update_admin_travel_match_settings(payload: dict, db: AsyncSession = Depends(get_db)):
    return await save_travel_match_settings(db, payload)


@router.get("/routes", response_model=list[RouteOut])
async def routes(db: AsyncSession = Depends(get_db)):
    return list(
        await db.scalars(
            select(TravelRoute).order_by(
                TravelRoute.display_weight.desc(),
                TravelRoute.id.desc(),
            )
        )
    )


@router.post("/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def create_route(payload: RouteCreate, db: AsyncSession = Depends(get_db)):
    item = TravelRoute(**payload.model_dump(), school_id=0, review_status="approved", reject_reason="")
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/routes/{route_id}", response_model=RouteOut)
async def update_route(route_id: int, payload: RouteUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelRoute, route_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    if item.school_id == 0:
        item.review_status = "approved"
        item.reject_reason = ""
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/routes/{route_id}/status", response_model=RouteOut)
async def toggle_route(route_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelRoute, route_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route not found")
    if enabled and item.review_status != "approved":
        raise HTTPException(status_code=400, detail="Route is not approved")
    item.status = enabled
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/routes/{route_id}/review", response_model=RouteOut)
async def review_route(route_id: int, payload: RouteReviewPayload, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelRoute, route_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route not found")
    item.review_status = "approved" if payload.approved else "rejected"
    item.reject_reason = "" if payload.approved else payload.reject_reason
    item.status = bool(payload.approved)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/announcements", response_model=list[AnnouncementOut])
async def announcements(db: AsyncSession = Depends(get_db)):
    return list(
        await db.scalars(
            select(PlatformAnnouncement).order_by(
                PlatformAnnouncement.pinned.desc(),
                PlatformAnnouncement.updated_at.desc(),
                PlatformAnnouncement.id.desc(),
            )
        )
    )


@router.post("/announcements", response_model=AnnouncementOut, status_code=status.HTTP_201_CREATED)
async def create_announcement(payload: AnnouncementCreate, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    if data["status"] and not data["published_at"]:
        data["published_at"] = datetime.now()
    item = PlatformAnnouncement(**data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(announcement_id: int, payload: AnnouncementUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    if item.status and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/announcements/{announcement_id}/status", response_model=AnnouncementOut)
async def toggle_announcement(announcement_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    item.status = enabled
    if enabled and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/announcements/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(announcement_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    await db.delete(item)
    await db.commit()


@router.get("/articles", response_model=list[ArticleOut])
async def articles(db: AsyncSession = Depends(get_db)):
    return list(
        await db.scalars(
            select(ContentArticle).order_by(
                ContentArticle.pinned.desc(),
                ContentArticle.sort_order.asc(),
                ContentArticle.updated_at.desc(),
                ContentArticle.id.desc(),
            )
        )
    )


@router.post("/articles/upload-image")
async def upload_article_image(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 jpg、png、gif、webp 图片")
    upload_setting = await get_upload_setting(db)
    max_image_size = upload_setting["max_image_bytes"]
    content = await file.read(max_image_size + 1)
    if len(content) > max_image_size:
        raise HTTPException(status_code=400, detail=f"图片不能超过 {upload_setting['max_image_mb']}MB")
    if not sniff_image_type(content, suffix):
        raise HTTPException(status_code=400, detail="图片内容与文件类型不匹配")
    stored = await save_upload_bytes(content, file.filename, file.content_type, "articles")
    asset = UploadedAsset(
        source="article",
        storage=stored.storage,
        bucket=stored.bucket,
        object_key=stored.object_key,
        path=stored.path,
        url=stored.url,
        filename=Path(stored.object_key).name,
        original_name=file.filename or "",
        mime_type=file.content_type or "",
        size=len(content),
        uploader_role="admin",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return {"id": asset.id, "url": asset_api_url(asset.id), "filename": asset.original_name}


async def ensure_article_slug_available(db: AsyncSession, slug: str, article_id: int | None = None):
    query = select(ContentArticle).where(ContentArticle.slug == slug)
    if article_id:
        query = query.where(ContentArticle.id != article_id)
    exists = await db.scalar(query)
    if exists:
        raise HTTPException(status_code=400, detail="Article slug already exists")


@router.post("/articles", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(payload: ArticleCreate, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    await ensure_article_slug_available(db, data["slug"])
    if data["status"] and not data["published_at"]:
        data["published_at"] = datetime.now()
    item = ContentArticle(**data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/articles/{article_id}", response_model=ArticleOut)
async def update_article(article_id: int, payload: ArticleUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(ContentArticle, article_id)
    if not item:
        raise HTTPException(status_code=404, detail="Article not found")
    data = payload.model_dump()
    await ensure_article_slug_available(db, data["slug"], article_id)
    for key, value in data.items():
        setattr(item, key, value)
    if item.status and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/articles/{article_id}/status", response_model=ArticleOut)
async def toggle_article(article_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(ContentArticle, article_id)
    if not item:
        raise HTTPException(status_code=404, detail="Article not found")
    item.status = enabled
    if enabled and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(article_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(ContentArticle, article_id)
    if not item:
        raise HTTPException(status_code=404, detail="Article not found")
    await db.delete(item)
    await db.commit()


@router.get("/schools", response_model=list[SchoolSiteOut])
async def school_sites(db: AsyncSession = Depends(get_db)):
    rows = list(
        await db.scalars(
            select(SchoolSite).order_by(
                SchoolSite.review_status.asc(),
                SchoolSite.display_weight.desc(),
                SchoolSite.sort_order.asc(),
                SchoolSite.id.desc(),
            )
        )
    )
    return [build_school_out(item) for item in rows]


@router.post("/schools", response_model=SchoolSiteOut, status_code=status.HTTP_201_CREATED)
async def create_school_site(payload: SchoolSiteCreate, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    merchant_password = data.pop("merchant_password", "")
    data["merchant_account"] = data.get("merchant_account") or None
    data["current"] = False
    data["status"] = data.get("review_status") == "approved" and data.get("status", True)
    if data.get("merchant_account"):
        duplicated = await db.scalar(select(SchoolSite).where(SchoolSite.merchant_account == data["merchant_account"]))
        if duplicated:
            raise HTTPException(status_code=400, detail="Merchant account already exists")
    if merchant_password:
        data["merchant_password_hash"] = hash_password(merchant_password)
    item = SchoolSite(**data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.put("/schools/{school_id}", response_model=SchoolSiteOut)
async def update_school_site(school_id: int, payload: SchoolSiteUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    data = payload.model_dump()
    data["merchant_account"] = data.get("merchant_account") or None
    data["current"] = False
    if data.get("review_status") != "approved":
        data["status"] = False
    if data.get("merchant_account"):
        duplicated = await db.scalar(
            select(SchoolSite).where(
                SchoolSite.merchant_account == data["merchant_account"],
                SchoolSite.id != school_id,
            )
        )
        if duplicated:
            raise HTTPException(status_code=400, detail="Merchant account already used by another school")
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.patch("/schools/{school_id}/status", response_model=SchoolSiteOut)
async def toggle_school_site(school_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    if enabled and item.review_status != "approved":
        raise HTTPException(status_code=400, detail="School is not approved")
    item.status = enabled
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.patch("/schools/{school_id}/current", response_model=SchoolSiteOut)
async def set_current_school_site(school_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    existing = list(await db.scalars(select(SchoolSite).where(SchoolSite.current.is_(True), SchoolSite.id != school_id)))
    for other in existing:
        other.current = False
    item.current = True
    item.status = True
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.patch("/schools/{school_id}/review", response_model=SchoolSiteOut)
async def review_school_site(school_id: int, payload: SchoolReviewPayload, db: AsyncSession = Depends(get_db)):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    item.review_status = "approved" if payload.approved else "rejected"
    item.reject_reason = "" if payload.approved else payload.reject_reason
    item.status = bool(payload.approved)
    if not payload.approved:
        item.current = False
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.patch("/schools/{school_id}/merchant-password", response_model=SchoolSiteOut)
async def update_school_merchant_password(
    school_id: int,
    payload: MerchantPasswordPayload,
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    duplicated = await db.scalar(
        select(SchoolSite).where(
            SchoolSite.merchant_account == payload.merchant_account,
            SchoolSite.id != school_id,
        )
    )
    if duplicated:
        raise HTTPException(status_code=400, detail="Merchant account already used by another school")
    item.merchant_account = payload.merchant_account
    item.merchant_password_hash = hash_password(payload.merchant_password)
    await db.commit()
    await db.refresh(item)
    return build_school_out(item)


@router.delete("/schools/{school_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_school_site(school_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(SchoolSite, school_id)
    if not item:
        raise HTTPException(status_code=404, detail="School site not found")
    await db.delete(item)
    await db.commit()


@router.get("/points/rule", response_model=PointRuleOut)
async def get_point_rule(db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        raise HTTPException(status_code=404, detail="Point rule not found")
    return rule


@router.put("/points/rule", response_model=PointRuleOut)
async def update_point_rule(payload: PointRulePayload, db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        rule = PointRule(id=1, **payload.model_dump())
        db.add(rule)
    else:
        for key, value in payload.model_dump().items():
            setattr(rule, key, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get("/orders", response_model=list[OrderOut])
async def orders(order_status: int | None = Query(default=None, alias="status"), db: AsyncSession = Depends(get_db)):
    query = select(TravelOrder).order_by(TravelOrder.id.desc())
    if order_status is not None:
        query = query.where(TravelOrder.status == order_status)
    rows = list(await db.scalars(query))
    changed = False
    for item in rows:
        before = item.fulfillment_status
        normalize_fulfillment_status(item)
        changed = changed or item.fulfillment_status != before
    if changed:
        await db.commit()
    return await attach_travel_order_details(db, rows)


@router.patch("/orders/{order_id}/contract/review", response_model=OrderOut)
async def review_order_contract(
    order_id: int,
    payload: ContractReviewPayload,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.contract_status not in {TravelContractStatus.PENDING, TravelContractStatus.REJECTED}:
        raise HTTPException(status_code=400, detail="该订单当前没有待审核合同")
    previous_status = str(item.contract_status or "")
    if payload.approved:
        item.contract_status = TravelContractStatus.APPROVED
        item.contract_reject_reason = ""
        if item.fulfillment_status in {
            "",
            TravelFulfillmentStatus.CONTRACT_PENDING,
            TravelFulfillmentStatus.CONTRACT_REVIEWING,
            TravelFulfillmentStatus.CONTRACT_REJECTED,
        }:
            item.fulfillment_status = TravelFulfillmentStatus.INFO_PENDING
    else:
        reason = payload.reject_reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="驳回合同时请填写原因")
        item.contract_status = TravelContractStatus.REJECTED
        item.contract_reject_reason = reason
        item.fulfillment_status = TravelFulfillmentStatus.CONTRACT_REJECTED
    item.contract_reviewed_at = datetime.now()
    add_travel_audit(
        db,
        item,
        event_type="contract_approved" if payload.approved else "contract_rejected",
        from_status=previous_status,
        to_status=str(item.contract_status),
        actor=admin_user,
        reason=item.contract_reject_reason,
        evidence={
            "contract_template_version": item.contract_template_version,
            "contract_template_snapshot_id": item.contract_template_snapshot_id,
        },
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/pickup/schedule", response_model=OrderOut)
async def schedule_order_pickup(
    order_id: int,
    payload: TravelPickupSchedulePayload,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.contract_status != TravelContractStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Contract must be approved before scheduling pickup")
    if not item.pickup_address:
        raise HTTPException(status_code=400, detail="User pickup information has not been submitted")
    if item.fulfillment_status in {
        TravelFulfillmentStatus.CHECKED_IN,
        TravelFulfillmentStatus.IN_TRIP,
        TravelFulfillmentStatus.COMPLETED,
        TravelFulfillmentStatus.CANCELLED,
    }:
        raise HTTPException(status_code=400, detail="Current order status cannot be scheduled")
    previous_status = str(item.fulfillment_status or "")
    item.pickup_time = payload.pickup_time.strip()
    item.pickup_location = payload.pickup_location.strip()
    item.driver_name = payload.driver_name.strip()
    item.driver_phone = payload.driver_phone.strip()
    item.vehicle_no = payload.vehicle_no.strip()
    item.pickup_notice = payload.pickup_notice.strip()
    item.fulfillment_status = TravelFulfillmentStatus.PICKUP_CONFIRMED
    item.pickup_confirmed_at = None
    item.qr_token = ""
    item.qr_issued_at = None
    item.qr_expires_at = None
    add_travel_audit(
        db,
        item,
        event_type="pickup_scheduled",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.PICKUP_CONFIRMED,
        actor=admin_user,
        evidence={
            "pickup_time": item.pickup_time,
            "pickup_location": item.pickup_location,
            "vehicle_no": item.vehicle_no,
        },
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/qr/issue", response_model=OrderOut)
async def issue_order_qr(
    order_id: int,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.fulfillment_status not in {TravelFulfillmentStatus.USER_CONFIRMED, TravelFulfillmentStatus.QR_ISSUED}:
        raise HTTPException(status_code=400, detail="User must confirm pickup before issuing QR")
    now = datetime.now()
    if not item.qr_token or not item.qr_expires_at or item.qr_expires_at <= now:
        item.qr_token = uuid4().hex
        item.qr_issued_at = now
        item.qr_expires_at = qr_expiry(item.travel_date)
        add_verification_record(
            db,
            item,
            action="issued",
            token=item.qr_token,
            result="success",
            operator=admin_user,
        )
    item.fulfillment_status = TravelFulfillmentStatus.QR_ISSUED
    add_travel_audit(
        db,
        item,
        event_type="qr_issued",
        from_status=TravelFulfillmentStatus.USER_CONFIRMED,
        to_status=TravelFulfillmentStatus.QR_ISSUED,
        actor=admin_user,
        evidence={"qr_expires_at": item.qr_expires_at.isoformat() if item.qr_expires_at else ""},
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/check-in", response_model=OrderOut)
async def check_in_order(
    order_id: int,
    token: str,
    device: str = Query(default="", max_length=255),
    location: str = Query(default="", max_length=255),
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.fulfillment_status not in {TravelFulfillmentStatus.USER_CONFIRMED, TravelFulfillmentStatus.QR_ISSUED}:
        add_verification_record(
            db,
            item,
            action="verify",
            token=token,
            result="rejected",
            operator=admin_user,
            device=device,
            location=location,
            reason="订单当前状态不可核销",
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Order cannot be checked in")
    if item.qr_expires_at and item.qr_expires_at <= datetime.now():
        add_verification_record(
            db,
            item,
            action="verify",
            token=token,
            result="expired",
            operator=admin_user,
            device=device,
            location=location,
            reason="核销码已过期",
        )
        item.qr_token = ""
        await db.commit()
        raise HTTPException(status_code=400, detail="Check-in token has expired")
    if not item.qr_token or token != item.qr_token:
        add_verification_record(
            db,
            item,
            action="verify",
            token=token,
            result="failed",
            operator=admin_user,
            device=device,
            location=location,
            reason="核销码无效",
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid check-in token")
    previous_status = str(item.fulfillment_status or "")
    used_token = item.qr_token
    item.checked_in_at = datetime.now()
    item.fulfillment_status = TravelFulfillmentStatus.CHECKED_IN
    item.qr_token = ""
    add_verification_record(
        db,
        item,
        action="verify",
        token=used_token,
        result="success",
        operator=admin_user,
        device=device,
        location=location,
    )
    add_travel_audit(
        db,
        item,
        event_type="travel_checked_in",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.CHECKED_IN,
        actor=admin_user,
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/trip/start", response_model=OrderOut)
async def start_order_trip(
    order_id: int,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.fulfillment_status not in {TravelFulfillmentStatus.CHECKED_IN, TravelFulfillmentStatus.IN_TRIP}:
        raise HTTPException(status_code=400, detail="Order must be checked in before trip start")
    previous_status = str(item.fulfillment_status or "")
    item.fulfillment_status = TravelFulfillmentStatus.IN_TRIP
    add_travel_audit(
        db,
        item,
        event_type="trip_started",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.IN_TRIP,
        actor=admin_user,
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/complete", response_model=OrderOut)
async def complete_order(
    order_id: int,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.fulfillment_status not in {
        TravelFulfillmentStatus.CHECKED_IN,
        TravelFulfillmentStatus.IN_TRIP,
        TravelFulfillmentStatus.COMPLETED,
    }:
        raise HTTPException(status_code=400, detail="Order must be checked in before completion")
    previous_status = str(item.fulfillment_status or "")
    item.fulfillment_status = TravelFulfillmentStatus.COMPLETED
    item.completed_at = datetime.now()
    item.status = 1
    add_travel_audit(
        db,
        item,
        event_type="order_completed",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.COMPLETED,
        actor=admin_user,
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/exception", response_model=OrderOut)
async def mark_order_exception(
    order_id: int,
    payload: TravelOrderExceptionPayload,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    if item.fulfillment_status == TravelFulfillmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Completed order cannot be marked as exception")
    previous_status = str(item.fulfillment_status or "")
    item.fulfillment_status = TravelFulfillmentStatus.EXCEPTION
    item.exception_reason = payload.reason.strip()
    add_travel_audit(
        db,
        item,
        event_type="order_exception",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.EXCEPTION,
        actor=admin_user,
        reason=item.exception_reason,
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/orders/{order_id}/cancel", response_model=OrderOut)
async def admin_cancel_travel_order(
    order_id: int,
    payload: TravelOrderCancelPayload,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await cancel_travel_order(
        db,
        order_id,
        actor=admin_user,
        reason=payload.reason,
        admin_override=True,
    )


@router.get("/orders/{order_id}/audit")
async def travel_order_audit(order_id: int, db: AsyncSession = Depends(get_db)):
    order = await db.get(TravelOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    events = list(
        await db.scalars(
            select(TravelOrderAuditLog)
            .where(TravelOrderAuditLog.order_id == order_id)
            .order_by(TravelOrderAuditLog.id)
        )
    )
    verifications = list(
        await db.scalars(
            select(TravelVerificationRecord)
            .where(TravelVerificationRecord.order_id == order_id)
            .order_by(TravelVerificationRecord.id)
        )
    )
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "events": events,
        "verifications": verifications,
    }


@router.get("/invites", response_model=list[InviteOut])
async def invites(abnormal: bool | None = None, db: AsyncSession = Depends(get_db)):
    query = select(InviteRelation).order_by(InviteRelation.id.desc())
    if abnormal is not None:
        query = query.where(InviteRelation.abnormal.is_(abnormal))
    return list(await db.scalars(query))


@router.patch("/invites/{invite_id}/freeze", response_model=InviteOut)
async def freeze_invite(invite_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(InviteRelation, invite_id)
    if not item:
        raise HTTPException(status_code=404, detail="Invite not found")
    item.frozen = True
    item.score_granted = False
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/invites/backfill-rewards")
async def backfill_invite_rewards(db: AsyncSession = Depends(get_db)):
    """Grant invite points for pending, valid invite relations.

    Covers the case where a registration was approved while the invite point
    rule was disabled: the relation stays score_granted=false and is picked up
    here once the rule is re-enabled. Idempotency is guaranteed by the
    credit_locked idempotency key (invite-reward:{relation.id}).
    """
    rule = await db.get(PointRule, 1)
    invite_enabled = bool((rule.enabled and rule.invite_enabled) if rule else True)
    invite_score = int(rule.invite_score if rule else 1)
    if not invite_enabled or invite_score <= 0:
        return {"ok": True, "granted": 0, "skipped": 0, "reason": "rule_disabled"}

    relations = list(
        await db.scalars(
            select(InviteRelation).where(
                InviteRelation.score_granted.is_(False),
                InviteRelation.abnormal.is_(False),
                InviteRelation.frozen.is_(False),
            )
        )
    )
    granted = 0
    skipped = 0
    for relation in relations:
        invitee = await db.scalar(select(UserAccount).where(UserAccount.phone == relation.invitee_phone))
        if not invitee or not invitee.is_registered or invitee.status != "active":
            skipped += 1
            continue
        inviter = await db.scalar(
            select(UserAccount).where(
                UserAccount.user_no == relation.inviter_id,
                UserAccount.role == "user",
                UserAccount.status == "active",
            )
        )
        if not inviter:
            skipped += 1
            continue
        inviter = await PointService.lock_user(db, inviter.id)
        valid_days = max(1, int(rule.valid_days if rule else 365))
        await PointService.credit_locked(
            db,
            inviter,
            invite_score,
            biz_type="invite_reward",
            biz_id=relation.id,
            biz_no=str(relation.id),
            idempotency_key=f"invite-reward:{relation.id}",
            remark=f"补发邀请积分（{relation.invitee_phone}）",
            expires_at=datetime.now() + timedelta(days=valid_days),
        )
        relation.score_granted = True
        granted += 1
    await db.commit()
    return {"ok": True, "granted": granted, "skipped": skipped, "reason": "ok"}


@router.get("/users", response_model=AdminUsersResponse)
async def users(registered_only: bool = False, db: AsyncSession = Depends(get_db)):
    query = select(UserAccount).order_by(UserAccount.created_at.desc())
    if registered_only:
        query = query.where(UserAccount.is_registered.is_(True))
    all_users = list(await db.scalars(query))
    identity_map = await resolve_user_identities(db, [user.id for user in all_users if user.role == "user"])
    conversations = list(await db.scalars(select(SupportConversation)))
    graduation_items = list(await db.scalars(select(GraduationCertification)))
    graduation_map = {item.user_id: item for item in graduation_items}
    conv_map: dict[str, list[SupportConversation]] = {}
    for item in conversations:
        conv_map.setdefault(item.user_id, []).append(item)
    rows = []
    for user in all_users:
        if user.role != "user":
            continue
        user_conversations = conv_map.get(user.user_no, [])
        rows.append(build_user_row(user, user_conversations, graduation_map.get(user.id), identity_map.get(user.id, "学员")))
    admins = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "admin")) or 0
    users_count = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "user")) or 0
    registered = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.is_registered.is_(True))
    ) or 0
    active = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.status == "active")
    ) or 0
    pending = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.status == "pending")
    ) or 0
    return AdminUsersResponse(
        summary=UserSummaryOut(
            total_users=users_count,
            registered_users=registered,
            admin_users=admins,
            active_users=active,
            pending_users=pending,
        ),
        users=rows,
    )


@router.get("/vip/orders")
async def vip_orders(db: AsyncSession = Depends(get_db)):
    items = list(await db.scalars(select(CommerceOrderItem).where(CommerceOrderItem.product_type == "vip").order_by(CommerceOrderItem.id.desc())))
    if not items:
        return []
    order_ids = {item.order_id for item in items}
    orders = {item.id: item for item in list(await db.scalars(select(CommerceOrder).where(CommerceOrder.id.in_(order_ids))))}
    user_ids = {order.user_id for order in orders.values()}
    users = {item.id: item for item in list(await db.scalars(select(UserAccount).where(UserAccount.id.in_(user_ids))))}
    return [{
        "order_no": orders[item.order_id].order_no,
        "user_id": orders[item.order_id].user_id,
        "user_name": users.get(orders[item.order_id].user_id).nickname if users.get(orders[item.order_id].user_id) else "-",
        "phone": users.get(orders[item.order_id].user_id).phone if users.get(orders[item.order_id].user_id) else "-",
        "plan_name": item.product_name,
        "amount": orders[item.order_id].payable_amount,
        "payment_status": orders[item.order_id].payment_status,
        "status": orders[item.order_id].status,
        "created_at": orders[item.order_id].created_at,
        "paid_at": orders[item.order_id].paid_at,
    } for item in items if item.order_id in orders]


@router.get("/vip/users")
async def vip_users(db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(select(UserAccount).where(UserAccount.role == "user", UserAccount.vip_enabled.is_(True)).order_by(UserAccount.vip_expires_at.desc(), UserAccount.id.desc())))
    return [{"id": user.id, "user_no": user.user_no, "nickname": user.nickname, "phone": user.phone, "vip_active": is_vip_active(user), "vip_type": user.vip_type, "vip_started_at": user.vip_started_at, "vip_expires_at": user.vip_expires_at, "vip_remark": user.vip_remark} for user in rows]


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def user_detail(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    conversations = list(
        await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no))
    )
    graduation = await db.scalar(
        select(GraduationCertification).where(GraduationCertification.user_id == user.id)
    )
    identity = await resolve_user_identity(db, user.id)
    return AdminUserDetail(
        user=build_user_row(user, conversations, graduation, identity),
        graduation_certification=graduation,
    )


@router.patch("/users/{user_id}/graduation/review", response_model=AdminUserDetail)
async def review_graduation_certification(
    user_id: int,
    payload: GraduationReviewPayload,
    admin: UserAccount | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    graduation = await db.scalar(
        select(GraduationCertification).where(GraduationCertification.user_id == user.id)
    )
    if not graduation:
        raise HTTPException(status_code=404, detail="Graduation certification not submitted")
    if not payload.approved and not payload.reject_reason.strip():
        raise HTTPException(status_code=400, detail="驳回时请填写原因")
    graduation.status = "approved" if payload.approved else "rejected"
    graduation.reject_reason = "" if payload.approved else payload.reject_reason.strip()
    graduation.reviewed_by = admin.id if admin else None
    graduation.reviewed_at = datetime.now()
    await db.commit()
    await db.refresh(graduation)
    conversations = list(
        await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no))
    )
    return AdminUserDetail(
        user=build_user_row(user, conversations, graduation, await resolve_user_identity(db, user.id)),
        graduation_certification=graduation,
    )


@router.get("/registrations", response_model=list[RegistrationRow])
async def registrations(db: AsyncSession = Depends(get_db)):
    query = (
        select(UserAccount)
        .where(UserAccount.role == "user", UserAccount.status == "pending")
        .order_by(UserAccount.created_at.desc())
    )
    return list(await db.scalars(query))


@router.patch("/registrations/{user_id}/review", response_model=AdminUserRow)
async def review_registration(user_id: int, payload: RegistrationReviewPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    user.status = "active" if payload.approved else "rejected"
    user.is_registered = bool(payload.approved)
    if payload.approved:
        relation = await db.scalar(
            select(InviteRelation).where(
                InviteRelation.invitee_phone == user.phone,
                InviteRelation.score_granted.is_(False),
                InviteRelation.abnormal.is_(False),
                InviteRelation.frozen.is_(False),
            )
        )
        rule = await db.get(PointRule, 1)
        invite_enabled = bool((rule.enabled and rule.invite_enabled) if rule else True)
        invite_score = int(rule.invite_score if rule else 1)
        if relation and invite_enabled and invite_score > 0:
            inviter = await db.scalar(
                select(UserAccount).where(
                    UserAccount.user_no == relation.inviter_id,
                    UserAccount.role == "user",
                    UserAccount.status == "active",
                )
            )
            if inviter:
                inviter = await PointService.lock_user(db, inviter.id)
                valid_days = max(1, int(rule.valid_days if rule else 365))
                await PointService.credit_locked(
                    db,
                    inviter,
                    invite_score,
                    biz_type="invite_reward",
                    biz_id=relation.id,
                    biz_no=str(relation.id),
                    idempotency_key=f"invite-reward:{relation.id}",
                    remark=f"邀请用户 {user.user_no} 注册审核通过",
                    expires_at=datetime.now() + timedelta(days=valid_days),
                )
                relation.score_granted = True
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.patch("/users/{user_id}/status", response_model=AdminUserRow)
async def update_user_status(user_id: int, payload: StatusUpdatePayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    user.status = payload.status
    user.is_registered = payload.status == "active"
    if payload.status in {"disabled", "cancelled", "rejected"}:
        await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.patch("/users/{user_id}/vip", response_model=AdminUserRow)
async def update_user_vip(user_id: int, payload: VipUpdatePayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    user.vip_enabled = payload.enabled
    user.vip_type = payload.vip_type.strip() if payload.enabled else ""
    user.vip_started_at = payload.started_at if payload.enabled else None
    user.vip_expires_at = payload.expires_at if payload.enabled else None
    user.vip_remark = payload.remark.strip()
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.patch("/users/{user_id}/password", response_model=AdminUserRow)
async def reset_user_password(user_id: int, payload: PasswordResetPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.post("/users/{user_id}/wallet/adjust", response_model=AdminUserRow)
async def adjust_user_wallet(user_id: int, payload: WalletAdjustPayload, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(UserAccount).where(UserAccount.id == user_id, UserAccount.role == "user").with_for_update())
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    amount = Decimal(payload.amount).quantize(Decimal("0.01"))
    before = Decimal(user.balance or 0).quantize(Decimal("0.01"))
    after = before + amount if payload.direction == "income" else before - amount
    if after < 0:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    user.balance = after
    db.add(
        WalletTransaction(
            user_id=user.id,
            user_no=user.user_no,
            transaction_no=f"WA{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:8].upper()}",
            direction=payload.direction,
            amount=amount,
            balance_before=before,
            balance_after=after,
            biz_type="admin_adjust",
            biz_id=user.id,
            biz_no=user.user_no,
            remark=payload.remark or ("admin income" if payload.direction == "income" else "admin expense"),
        )
    )
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.post("/users/{user_id}/points/adjust", response_model=AdminUserRow)
async def adjust_user_points(
    user_id: int,
    payload: PointsAdjustPayload,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await PointService.lock_user(db, user_id)
    if user.role != "user":
        raise HTTPException(status_code=404, detail="User not found")
    adjustment_no = f"PA{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:8].upper()}"
    if payload.direction == "income":
        rule = await db.get(PointRule, 1)
        valid_days = max(1, int(rule.valid_days if rule else 365))
        await PointService.credit_locked(
            db,
            user,
            payload.amount,
            biz_type="admin_adjust",
            biz_id=admin_user.id,
            biz_no=adjustment_no,
            idempotency_key=f"admin-points:{adjustment_no}",
            remark=payload.remark or "平台增加积分",
            expires_at=datetime.now() + timedelta(days=valid_days),
        )
    else:
        await PointService.debit_locked(
            db,
            user,
            payload.amount,
            biz_type="admin_adjust",
            biz_id=admin_user.id,
            biz_no=adjustment_no,
            idempotency_key=f"admin-points:{adjustment_no}",
            remark=payload.remark or "平台扣减积分",
        )
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations, exam_status=await resolve_user_identity(db, user.id))


@router.get("/preferences")
async def preference_insights(db: AsyncSession = Depends(get_db)):
    events = list(await db.scalars(select(PreferenceEvent).order_by(PreferenceEvent.created_at.desc())))
    grouped: dict[tuple[str, str, str], dict] = {}
    users: dict[str, dict] = {}
    for event in events:
        key = (event.user_id, event.preference_type, event.target_key)
        item = grouped.setdefault(
            key,
            {
                "user_id": event.user_id,
                "user_name": event.user_name,
                "preference_type": event.preference_type,
                "target_key": event.target_key,
                "target_name": event.target_name,
                "score": 0,
                "actions": 0,
                "updated_at": event.created_at,
            },
        )
        item["score"] += event.score
        item["actions"] += 1
        if event.created_at > item["updated_at"]:
            item["updated_at"] = event.created_at
        user = users.setdefault(
            event.user_id,
            {
                "user_id": event.user_id,
                "user_name": event.user_name,
                "route_score": 0,
                "study_score": 0,
                "actions": 0,
                "updated_at": event.created_at,
                "interests": [],
            },
        )
        user[f"{event.preference_type}_score"] += event.score
        user["actions"] += 1
        if event.created_at > user["updated_at"]:
            user["updated_at"] = event.created_at

    active = [item for item in grouped.values() if item["score"] > 0]
    for item in active:
        users[item["user_id"]]["interests"].append(item)
    user_rows = []
    for user in users.values():
        interests = sorted(user.pop("interests"), key=lambda x: x["score"], reverse=True)
        top = interests[0] if interests else None
        user["top_interest"] = top["target_name"] if top else "鏆傛棤鏄庣‘鍋忓ソ"
        user["top_type"] = top["preference_type"] if top else ""
        user["interest_score"] = max(user["route_score"], 0) + max(user["study_score"], 0)
        user_rows.append(user)

    heat: dict[tuple[str, str], dict] = {}
    for item in active:
        key = (item["preference_type"], item["target_name"])
        rank = heat.setdefault(
            key,
            {"type": item["preference_type"], "name": item["target_name"], "score": 0, "users": set()},
        )
        rank["score"] += item["score"]
        rank["users"].add(item["user_id"])
    rankings = sorted([{**item, "users": len(item["users"])} for item in heat.values()], key=lambda x: x["score"], reverse=True)
    return {
        "summary": {
            "users": len(users),
            "events": len(events),
            "route_score": sum(max(u["route_score"], 0) for u in users.values()),
            "study_score": sum(max(u["study_score"], 0) for u in users.values()),
        },
        "rankings": rankings,
        "users": sorted(user_rows, key=lambda x: x["interest_score"], reverse=True),
    }


@router.get("/assets/images", response_model=list[UploadedAssetOut])
async def image_assets(
    source: str | None = None,
    storage: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    await backfill_local_image_assets(db)
    query = select(UploadedAsset).order_by(UploadedAsset.id.desc())
    if source:
        query = query.where(UploadedAsset.source == source)
    if storage:
        query = query.where(UploadedAsset.storage == storage)
    return list(await db.scalars(query))


@router.post("/commerce/orders/{order_no}/refund")
async def refund_commerce_order(
    order_no: str,
    admin_user: UserAccount = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Refund a paid standard order through its original payment channel."""
    locked_order = await db.scalar(
        select(CommerceOrder).where(CommerceOrder.order_no == order_no).with_for_update()
    )
    if not locked_order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if locked_order.payment_status == CommercePaymentStatus.REFUNDED:
        return {"ok": True, "order_no": order_no, "status": "refunded", "amount": str(locked_order.payable_amount)}
    if locked_order.refund_status == "processing" and locked_order.payment_method == "wechat":
        result = await query_wechat_refund(locked_order.refund_no or "")
        provider_status, provider_refund_id = validate_refund_result(
            locked_order,
            result,
            locked_order.refund_no or "",
        )
        if provider_status == "SUCCESS":
            locked_order = await OrderService.finalize_refund(
                db,
                locked_order,
                refund_no=locked_order.refund_no or "",
                refund_id=provider_refund_id,
                wallet_refund=False,
            )
        else:
            locked_order.refund_status = provider_status.lower()
            locked_order.refund_id = provider_refund_id
            await db.commit()
        return {
            "ok": True,
            "order_no": order_no,
            "status": locked_order.refund_status,
            "amount": str(locked_order.payable_amount),
        }
    if locked_order.payment_status != CommercePaymentStatus.PAID or locked_order.status != CommerceOrderStatus.PAID:
        raise BusinessError("仅已支付订单可以退款", status_code=400, code="ORDER_NOT_REFUNDABLE")
    refund_no = locked_order.refund_no or SnowflakeGenerator(
        settings.snowflake_worker_id, settings.snowflake_datacenter_id
    ).next_order_no("RF")
    if locked_order.payment_method == "wechat":
        result = await create_wechat_refund(locked_order, refund_no)
        provider_status_raw, provider_refund_id = validate_refund_result(locked_order, result, refund_no)
        provider_status = provider_status_raw.lower()
        locked_order.refund_no = refund_no
        locked_order.refund_id = provider_refund_id
        locked_order.refund_status = provider_status
        if provider_status == "success":
            locked_order = await OrderService.finalize_refund(
                db,
                locked_order,
                refund_no=refund_no,
                refund_id=locked_order.refund_id,
                wallet_refund=False,
            )
        else:
            await db.commit()
        return {
            "ok": True,
            "order_no": order_no,
            "status": locked_order.refund_status,
            "amount": str(locked_order.payable_amount),
            "refunded_by": admin_user.id,
        }

    locked_order = await OrderService.finalize_refund(
        db,
        locked_order,
        refund_no=refund_no,
        wallet_refund=locked_order.payment_method == "balance",
    )
    return {
        "ok": True,
        "order_no": order_no,
        "status": "refunded",
        "amount": str(locked_order.payable_amount),
        "refunded_by": admin_user.id,
    }


async def backfill_local_image_assets(db: AsyncSession) -> None:
    upload_root = Path(settings.upload_dir)
    if not upload_root.exists():
        return
    known = set(await db.scalars(select(UploadedAsset.object_key).where(UploadedAsset.storage == "local")))
    image_suffixes = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    added = False
    for file in upload_root.rglob("*"):
        if not file.is_file() or file.suffix.lower() not in image_suffixes:
            continue
        object_key = file.relative_to(upload_root).as_posix()
        if object_key in known:
            continue
        if object_key.startswith("graduation/"):
            source = "student_card"
        elif object_key.startswith("support-thumbs/"):
            source = "support_thumb"
        elif object_key.startswith("support/"):
            source = "support"
        elif object_key.startswith("contracts/"):
            source = "contract_signature"
        elif object_key.startswith("travel-reviews/"):
            source = "travel_review"
        else:
            source = "common"
        db.add(
            UploadedAsset(
                source=source,
                storage="local",
                bucket="",
                object_key=object_key,
                path=f"/uploads/{object_key}",
                url=f"/uploads/{object_key}",
                filename=file.name,
                original_name=file.name,
                mime_type={
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(file.suffix.lower(), "image/*"),
                size=file.stat().st_size,
                uploader_role="system",
            )
        )
        added = True
    if added:
        await db.commit()


