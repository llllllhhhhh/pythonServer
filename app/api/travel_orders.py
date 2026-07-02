from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.contract_settings import get_contract_template
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.snowflake import SnowflakeGenerator
from app.models import TravelOrder, TravelRoute, UserAccount
from app.schemas.api import (
    ContractSignPayload,
    ContractTemplateOut,
    OrderOut,
    RouteExchangePayload,
    TravelPickupInfoPayload,
)

router = APIRouter(tags=["travel-orders"])
snowflake = SnowflakeGenerator(settings.snowflake_worker_id, settings.snowflake_datacenter_id)


def _phone_tail(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def _travel_order_owned_by(item: TravelOrder, user: UserAccount) -> bool:
    """Check ownership while keeping older seeded masked-phone orders visible."""
    return (
        item.user_id == user.id
        or item.user_no == user.user_no
        or (_phone_tail(item.phone) and _phone_tail(item.phone) == _phone_tail(user.phone))
    )


def normalize_fulfillment_status(item: TravelOrder) -> None:
    if item.contract_status == "approved" and item.fulfillment_status in {"", "contract_pending", "contract_reviewing"}:
        item.fulfillment_status = "info_pending"


async def _get_my_travel_order(db: AsyncSession, order_id: int, user: UserAccount) -> TravelOrder:
    item = await db.get(TravelOrder, order_id)
    if not item or not _travel_order_owned_by(item, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="旅行订单不存在")
    normalize_fulfillment_status(item)
    return item


@router.get("/travel/orders", response_model=list[OrderOut])
async def my_travel_orders(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TravelOrder]:
    """List travel orders owned by the current user."""
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
    return visible


@router.get("/travel/contract-template", response_model=ContractTemplateOut)
async def travel_contract_template(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the active travel contract template managed by the platform."""
    return await get_contract_template(db)


@router.post("/travel/routes/{route_id}/exchange", response_model=OrderOut)
async def exchange_travel_route(
    route_id: int,
    payload: RouteExchangePayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    """Exchange user points for an on-shelf travel route and create a travel order."""
    route = await db.scalar(
        select(TravelRoute)
        .where(TravelRoute.id == route_id, TravelRoute.status.is_(True))
        .with_for_update()
    )
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="路线不存在或已下架")
    if int(route.stock or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该路线库存不足")
    locked_user = await db.scalar(
        select(UserAccount)
        .where(UserAccount.id == current_user.id)
        .with_for_update()
    )
    if not locked_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    required_points = int(route.price or 0)
    if int(locked_user.points or 0) < required_points:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"积分不足，还差 {required_points - int(locked_user.points or 0)} 积分")

    locked_user.points = int(locked_user.points or 0) - required_points
    route.stock = int(route.stock or 0) - 1
    order = TravelOrder(
        order_no=snowflake.next_order_no("TR"),
        user_id=locked_user.id,
        user_no=locked_user.user_no,
        order_type="积分兑换",
        title=route.name,
        user_name=locked_user.nickname,
        phone=locked_user.phone,
        travel_date=payload.travel_date.strip(),
        agency=route.agency,
        amount_text=f"{required_points} 积分",
        status=0,
        contract_status="unsigned",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/travel/orders/{order_id}/contract/sign", response_model=OrderOut)
async def sign_travel_contract(
    order_id: int,
    payload: ContractSignPayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    """Submit a travel contract signature for platform review."""
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status == "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="合同已审核通过，无需重复签署")
    item.user_id = current_user.id
    item.user_no = current_user.user_no
    item.user_name = current_user.nickname or payload.signer_name.strip()
    item.phone = current_user.phone or payload.signer_phone.strip()
    item.contract_status = "pending"
    item.fulfillment_status = "contract_reviewing"
    item.contract_signer_name = payload.signer_name.strip()
    item.contract_signer_phone = payload.signer_phone.strip()
    item.contract_id_no = payload.id_no.strip()
    item.travel_date = payload.travel_date.strip()
    item.contract_signature_data = payload.signature_data
    item.contract_signed_at = datetime.now()
    item.contract_reviewed_at = None
    item.contract_reject_reason = ""
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/travel/orders/{order_id}/pickup-info", response_model=OrderOut)
async def submit_travel_pickup_info(
    order_id: int,
    payload: TravelPickupInfoPayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    """Submit pickup/contact information after the contract has been approved."""
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status != "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contract must be approved before submitting pickup information")
    if item.fulfillment_status in {"checked_in", "in_trip", "completed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pickup information can no longer be changed")
    item.pickup_address = payload.pickup_address.strip()
    item.pickup_detail = payload.pickup_detail.strip()
    item.traveler_count = payload.traveler_count
    item.emergency_contact = payload.emergency_contact.strip()
    item.emergency_phone = payload.emergency_phone.strip()
    item.luggage_count = payload.luggage_count
    item.pickup_note = payload.pickup_note.strip()
    item.fulfillment_status = "info_submitted"
    item.pickup_confirmed_at = None
    item.qr_token = ""
    item.qr_issued_at = None
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/travel/orders/{order_id}/pickup/confirm", response_model=OrderOut)
async def confirm_travel_pickup(
    order_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TravelOrder:
    """Confirm the platform pickup schedule and issue a check-in token."""
    item = await _get_my_travel_order(db, order_id, current_user)
    if item.contract_status != "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contract has not been approved")
    if item.fulfillment_status != "pickup_confirmed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pickup schedule has not been confirmed by the platform")
    item.fulfillment_status = "user_confirmed"
    item.pickup_confirmed_at = datetime.now()
    if not item.qr_token:
        item.qr_token = uuid4().hex
        item.qr_issued_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item
