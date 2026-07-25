import base64
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.api.merchant import get_current_school
from app.core.config import settings
from app.core.contract_settings import get_contract_template
from app.core.database import get_db
from app.core.order_status import TravelContractStatus, TravelFulfillmentStatus
from app.core.security import get_current_user, require_admin
from app.core.snowflake import SnowflakeGenerator
from app.core.storage import save_upload_bytes
from app.core.upload_settings import get_upload_setting
from app.models import ContractTemplateSnapshot, SchoolSite, TravelOrder, TravelRoute, TravelRouteReview, UploadedAsset, UserAccount
from app.schemas.api import (
    ContractSignPayload,
    ContractTemplateOut,
    OrderOut,
    RouteExchangePayload,
    TravelRouteReviewCreate,
    TravelRouteReviewEligibilityOut,
    TravelRouteReviewListOut,
    TravelRouteReviewOut,
    TravelPickupInfoPayload,
)

router = APIRouter(tags=["travel-orders"])
snowflake = SnowflakeGenerator(settings.snowflake_worker_id, settings.snowflake_datacenter_id)


def _phone_tail(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


async def _save_contract_signature(
    db: AsyncSession,
    signature_data: str,
    order_no: str,
    user_id: int,
) -> str:
    data = (signature_data or "").strip()
    if not data.startswith("data:") or "," not in data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid signature image")
    header, payload = data.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower() or "image/png"
    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(mime)
    if not suffix or not mime.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported signature image type")
    try:
        if ";base64" in header:
            content = base64.b64decode(payload, validate=True)
        else:
            from urllib.parse import unquote_to_bytes

            content = unquote_to_bytes(payload)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid signature image data")
    if len(content) < 32:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Signature image is empty")
    if len(content) > 500_000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Signature image is too large")
    stored = await save_upload_bytes(content, f"{order_no}-signature{suffix}", mime, "contracts/signatures")
    db.add(
        UploadedAsset(
            source="contract_signature",
            storage=stored.storage,
            bucket=stored.bucket,
            object_key=stored.object_key,
            path=stored.path,
            url=stored.url,
            filename=f"{order_no}-signature{suffix}",
            original_name=f"{order_no}-signature{suffix}",
            mime_type=mime,
            size=len(content),
            uploader_role="user",
            user_id=user_id,
        )
    )
    return stored.url


def _travel_order_owned_by(item: TravelOrder, user: UserAccount) -> bool:
    return (
        item.user_id == user.id
        or item.user_no == user.user_no
        or (_phone_tail(item.phone) and _phone_tail(item.phone) == _phone_tail(user.phone))
    )


def normalize_fulfillment_status(item: TravelOrder) -> None:
    if item.contract_status == TravelContractStatus.APPROVED and item.fulfillment_status in {
        "",
        TravelFulfillmentStatus.CONTRACT_PENDING,
        TravelFulfillmentStatus.CONTRACT_REVIEWING,
    }:
        item.fulfillment_status = TravelFulfillmentStatus.INFO_PENDING


async def attach_route_images(db: AsyncSession, orders: list[TravelOrder]) -> list[TravelOrder]:
    route_ids = {item.route_id for item in orders if item.route_id and not item.cover}
    route_image_map: dict[int, str] = {}
    if route_ids:
        routes = list(await db.scalars(select(TravelRoute).where(TravelRoute.id.in_(route_ids))))
        route_image_map = {route.id: route.image for route in routes if route.image}

    legacy_titles = {item.title for item in orders if item.title and not item.cover and not item.route_id}
    legacy_image_map: dict[str, str] = {}
    if legacy_titles:
        routes = list(await db.scalars(select(TravelRoute).where(TravelRoute.name.in_(legacy_titles))))
        legacy_image_map = {route.name: route.image for route in routes if route.image}

    for item in orders:
        image = item.cover or route_image_map.get(item.route_id, "") or legacy_image_map.get(item.title, "")
        setattr(item, "image", image)
    return orders


async def attach_contract_snapshots(db: AsyncSession, orders: list[TravelOrder]) -> list[TravelOrder]:
    snapshot_ids = {item.contract_template_snapshot_id for item in orders if item.contract_template_snapshot_id}
    versions = {item.contract_template_version for item in orders if item.contract_template_version and not item.contract_template_snapshot_id}
    snapshots: list[ContractTemplateSnapshot] = []
    if snapshot_ids:
        snapshots.extend(list(await db.scalars(select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.id.in_(snapshot_ids)))))
    if versions:
        snapshots.extend(list(await db.scalars(select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.version.in_(versions)))))
    by_id = {item.id: item for item in snapshots}
    by_version = {item.version: item for item in snapshots}
    for item in orders:
        snapshot = by_id.get(item.contract_template_snapshot_id) or by_version.get(item.contract_template_version)
        setattr(item, "contract_template_title", snapshot.title if snapshot else "")
        setattr(item, "contract_template_content", snapshot.content if snapshot else "")
        setattr(item, "contract_template_travel_date_days", snapshot.travel_date_days if snapshot else 0)
    return orders


async def attach_travel_order_details(db: AsyncSession, orders: list[TravelOrder]) -> list[TravelOrder]:
    await attach_route_images(db, orders)
    await attach_contract_snapshots(db, orders)
    for item in orders:
        for field in (
            "cover",
            "contract_signature_data",
            "contract_signature_url",
            "pickup_address",
            "pickup_detail",
            "emergency_contact",
            "emergency_phone",
            "pickup_note",
            "pickup_time",
            "pickup_location",
            "driver_name",
            "driver_phone",
            "vehicle_no",
            "pickup_notice",
            "qr_token",
            "exception_reason",
        ):
            if getattr(item, field, None) is None:
                setattr(item, field, "")
    return orders


async def _get_my_travel_order(db: AsyncSession, order_id: int, user: UserAccount) -> TravelOrder:
    item = await db.get(TravelOrder, order_id)
    if not item or not _travel_order_owned_by(item, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Travel order not found")
    normalize_fulfillment_status(item)
    return item


async def _my_route_orders(db: AsyncSession, route_id: int, user: UserAccount) -> list[TravelOrder]:
    phone_tail = _phone_tail(user.phone)
    query = (
        select(TravelOrder)
        .where(
            TravelOrder.route_id == route_id,
            or_(
                TravelOrder.user_id == user.id,
                TravelOrder.user_no == user.user_no,
                TravelOrder.phone.like(f"%{phone_tail}") if phone_tail else TravelOrder.id == -1,
            ),
        )
        .order_by(desc(TravelOrder.created_at), desc(TravelOrder.id))
    )
    rows = list(await db.scalars(query))
    return [item for item in rows if _travel_order_owned_by(item, user)]


async def _review_eligible_order(db: AsyncSession, route_id: int, user: UserAccount) -> tuple[list[TravelOrder], set[int], TravelOrder | None]:
    orders = await _my_route_orders(db, route_id, user)
    if not orders:
        return [], set(), None
    reviewed_order_ids = set(
        await db.scalars(
            select(TravelRouteReview.order_id).where(
                TravelRouteReview.order_id.in_([item.id for item in orders])
            )
        )
    )
    eligible = next((item for item in orders if item.id not in reviewed_order_ids), None)
    return orders, reviewed_order_ids, eligible


@router.get("/travel/orders", response_model=list[OrderOut])
async def my_travel_orders(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TravelOrder]:
    phone_tail = _phone_tail(current_user.phone)
    query = select(TravelOrder).where(
        or_(
            TravelOrder.user_id == current_user.id,
            TravelOrder.user_no == current_user.user_no,
            TravelOrder.phone.like(f"%{phone_tail}") if phone_tail else TravelOrder.id == -1,
        )
    ).order_by(desc(TravelOrder.created_at), desc(TravelOrder.id))
    rows = list(await db.scalars(query))
    visible = [item for item in rows if _travel_order_owned_by(item, current_user)]
    changed = False
    for item in visible:
        before = item.fulfillment_status
        normalize_fulfillment_status(item)
        changed = changed or item.fulfillment_status != before
    if changed:
        await db.commit()
    return await attach_travel_order_details(db, visible)


@router.get("/public/routes/{route_id}/reviews", response_model=TravelRouteReviewListOut)
async def public_route_reviews(
    route_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    route = await db.get(TravelRoute, route_id)
    if not route or not route.status or route.review_status != "approved":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    items = list(
        await db.scalars(
            select(TravelRouteReview)
            .where(
                TravelRouteReview.route_id == route_id,
                TravelRouteReview.status == "published",
            )
            .order_by(desc(TravelRouteReview.created_at), desc(TravelRouteReview.id))
        )
    )
    count = len(items)
    average_rating = round(sum(int(item.rating or 0) for item in items) / count, 1) if count else 0
    return {"route_id": route_id, "count": count, "average_rating": average_rating, "items": items}


@router.get("/travel/routes/{route_id}/review-eligibility", response_model=TravelRouteReviewEligibilityOut)
async def route_review_eligibility(
    route_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    route = await db.get(TravelRoute, route_id)
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    orders, reviewed_order_ids, eligible = await _review_eligible_order(db, route_id, current_user)
    if eligible:
        return {
            "route_id": route_id,
            "can_review": True,
            "reason": "",
            "eligible_order_id": eligible.id,
            "eligible_order_no": eligible.order_no,
            "reviewed_order_ids": list(reviewed_order_ids),
        }
    return {
        "route_id": route_id,
        "can_review": False,
        "reason": "already_reviewed" if orders else "not_exchanged",
        "eligible_order_id": 0,
        "eligible_order_no": "",
        "reviewed_order_ids": list(reviewed_order_ids),
    }


@router.post("/travel/routes/{route_id}/reviews", response_model=TravelRouteReviewOut, status_code=status.HTTP_201_CREATED)
async def create_route_review(
    route_id: int,
    payload: TravelRouteReviewCreate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelRouteReview:
    route = await db.get(TravelRoute, route_id)
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    orders, reviewed_order_ids, eligible = await _review_eligible_order(db, route_id, current_user)
    if not orders:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="兑换该路线后才能评价")
    order = None
    if payload.order_id:
        order = next((item for item in orders if item.id == payload.order_id), None)
        if not order:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="评价订单不属于当前用户")
        if order.id in reviewed_order_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该兑换订单已经评价过")
    else:
        order = eligible
    if not order:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该路线已评价过")
    images = [str(item).strip() for item in payload.images if str(item).strip()][:6]
    review = TravelRouteReview(
        route_id=route_id,
        order_id=order.id,
        order_no=order.order_no,
        user_id=current_user.id,
        user_no=current_user.user_no,
        user_name=current_user.nickname or "用户",
        rating=payload.rating,
        content=payload.content.strip(),
        images=images,
        status="published",
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该兑换订单已经评价过")
    await db.refresh(review)
    return review


@router.post("/travel/routes/{route_id}/reviews/upload")
async def upload_route_review_image(
    route_id: int,
    file: UploadFile = File(...),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    route = await db.get(TravelRoute, route_id)
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    orders, _, _ = await _review_eligible_order(db, route_id, current_user)
    if not orders:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="兑换该路线后才能上传评价图片")
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 jpg/png/gif/webp 图片")
    upload_setting = await get_upload_setting(db)
    max_image_size = upload_setting["max_image_bytes"]
    max_image_mb = upload_setting["max_image_mb"]
    content = await file.read(max_image_size + 1)
    if len(content) > max_image_size:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"图片不能超过 {max_image_mb}MB")
    stored = await save_upload_bytes(content, file.filename or f"route-review{suffix}", file.content_type or "image/jpeg", "travel-reviews")
    asset = UploadedAsset(
        source="travel_review",
        storage=stored.storage,
        bucket=stored.bucket,
        object_key=stored.object_key,
        path=stored.path,
        url=stored.url,
        filename=Path(stored.object_key).name,
        original_name=file.filename or "",
        mime_type=file.content_type or "",
        size=len(content),
        uploader_role="user",
        user_id=current_user.id,
    )
    db.add(asset)
    await db.flush()
    public_url = f"{settings.api_prefix}/public/assets/{asset.id}/file"
    await db.commit()
    return {"url": public_url, "path": public_url, "storage": stored.storage}


@router.get("/admin/travel/route-reviews", response_model=list[TravelRouteReviewOut], dependencies=[Depends(require_admin)])
async def admin_route_reviews(
    route_id: int = Query(default=0),
    review_status: str = Query(default="all"),
    db: AsyncSession = Depends(get_db),
) -> list[TravelRouteReview]:
    query = select(TravelRouteReview)
    if route_id:
        query = query.where(TravelRouteReview.route_id == route_id)
    if review_status != "all":
        query = query.where(TravelRouteReview.status == review_status)
    return list(await db.scalars(query.order_by(desc(TravelRouteReview.created_at), desc(TravelRouteReview.id))))


@router.get("/merchant/travel/route-reviews", response_model=list[TravelRouteReviewOut])
async def merchant_route_reviews(
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
) -> list[TravelRouteReview]:
    route_ids = list(await db.scalars(select(TravelRoute.id).where(TravelRoute.school_id == school.id)))
    if not route_ids:
        return []
    return list(
        await db.scalars(
            select(TravelRouteReview)
            .where(TravelRouteReview.route_id.in_(route_ids))
            .order_by(desc(TravelRouteReview.created_at), desc(TravelRouteReview.id))
        )
    )


@router.get("/travel/contract-template", response_model=ContractTemplateOut)
async def travel_contract_template(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await get_contract_template(db)


@router.post("/travel/routes/{route_id}/exchange", response_model=OrderOut)
async def exchange_travel_route(
    route_id: int,
    payload: RouteExchangePayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    route = await db.scalar(
        select(TravelRoute)
        .where(
            TravelRoute.id == route_id,
            TravelRoute.status.is_(True),
            TravelRoute.review_status == "approved",
        )
        .with_for_update()
    )
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found or offline")
    if int(route.stock or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Route stock is not enough")
    locked_user = await db.scalar(
        select(UserAccount)
        .where(UserAccount.id == current_user.id)
        .with_for_update()
    )
    if not locked_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please login first")
    required_points = int(route.price or 0)
    if int(locked_user.points or 0) < required_points:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient points")

    locked_user.points = int(locked_user.points or 0) - required_points
    route.stock = int(route.stock or 0) - 1
    order = TravelOrder(
        order_no=snowflake.next_order_no("TR"),
        user_id=locked_user.id,
        user_no=locked_user.user_no,
        route_id=route.id,
        order_type="points_exchange",
        title=route.name,
        user_name=locked_user.nickname,
        phone=locked_user.phone,
        travel_date=payload.travel_date.strip(),
        agency=route.agency,
        amount_text=f"{required_points} 积分",
        cover=route.image or "",
        status=0,
        contract_status=TravelContractStatus.UNSIGNED,
        fulfillment_status=TravelFulfillmentStatus.CONTRACT_PENDING,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await attach_travel_order_details(db, [order])
    return order


@router.post("/travel/orders/{order_id}/contract/sign", response_model=OrderOut)
async def sign_travel_contract(
    order_id: int,
    payload: ContractSignPayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status == TravelContractStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contract already approved")
    template = await get_contract_template(db)
    item.user_id = current_user.id
    item.user_no = current_user.user_no
    item.user_name = current_user.nickname or payload.signer_name.strip()
    item.phone = current_user.phone or payload.signer_phone.strip()
    item.contract_status = TravelContractStatus.PENDING
    item.fulfillment_status = TravelFulfillmentStatus.CONTRACT_REVIEWING
    item.contract_signer_name = payload.signer_name.strip()
    item.contract_signer_phone = payload.signer_phone.strip()
    item.contract_id_no = payload.id_no.strip()
    item.travel_date = payload.travel_date.strip()
    item.contract_template_version = int(template.get("version") or template.get("contract_template_version") or 0)
    item.contract_template_snapshot_id = int(template.get("snapshot_id") or template.get("contract_template_snapshot_id") or 0)
    item.contract_signature_url = await _save_contract_signature(db, payload.signature_data, item.order_no, current_user.id)
    item.contract_signature_data = ""
    item.contract_signed_at = datetime.now()
    item.contract_reviewed_at = None
    item.contract_reject_reason = ""
    await db.commit()
    await db.refresh(item)
    await attach_travel_order_details(db, [item])
    return item


@router.put("/travel/orders/{order_id}/pickup-info", response_model=OrderOut)
async def submit_travel_pickup_info(
    order_id: int,
    payload: TravelPickupInfoPayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status != TravelContractStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contract must be approved before pickup information")
    if item.fulfillment_status in {
        TravelFulfillmentStatus.CHECKED_IN,
        TravelFulfillmentStatus.IN_TRIP,
        TravelFulfillmentStatus.COMPLETED,
        TravelFulfillmentStatus.CANCELLED,
    }:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pickup information can no longer be changed")
    item.pickup_address = payload.pickup_address.strip()
    item.pickup_detail = payload.pickup_detail.strip()
    item.traveler_count = payload.traveler_count
    item.emergency_contact = payload.emergency_contact.strip()
    item.emergency_phone = payload.emergency_phone.strip()
    item.luggage_count = payload.luggage_count
    item.pickup_note = payload.pickup_note.strip()
    item.fulfillment_status = TravelFulfillmentStatus.INFO_SUBMITTED
    item.pickup_confirmed_at = None
    item.qr_token = ""
    item.qr_issued_at = None
    await db.commit()
    await db.refresh(item)
    await attach_travel_order_details(db, [item])
    return item


@router.patch("/travel/orders/{order_id}/pickup/confirm", response_model=OrderOut)
async def confirm_travel_pickup(
    order_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status != TravelContractStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contract has not been approved")
    if item.fulfillment_status != TravelFulfillmentStatus.PICKUP_CONFIRMED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pickup schedule has not been confirmed")
    item.fulfillment_status = TravelFulfillmentStatus.USER_CONFIRMED
    item.pickup_confirmed_at = datetime.now()
    if not item.qr_token:
        item.qr_token = uuid4().hex
        item.qr_issued_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    await attach_travel_order_details(db, [item])
    return item
