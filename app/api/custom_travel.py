from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models import CustomTravelRequest, UserAccount
from app.schemas.api import (
    CustomTravelRequestCreate,
    CustomTravelRequestOut,
    CustomTravelReviewPayload,
)

router = APIRouter(tags=["custom-travel"])


def _new_request_no() -> str:
    """Create a short globally unique custom-travel request number."""
    return f"DZ{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"


def _normalize_list(value: list[str] | None) -> list[str]:
    """Drop empty strings and cap each text item to a safe display length."""
    return [str(item).strip()[:200] for item in (value or []) if str(item).strip()]


@router.post("/custom-travel/requests", response_model=CustomTravelRequestOut)
async def create_custom_travel_request(
    payload: CustomTravelRequestCreate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomTravelRequest:
    """Submit a manual deep-custom travel request for admin review."""
    item = CustomTravelRequest(
        request_no=_new_request_no(),
        user_id=current_user.id,
        user_no=current_user.user_no,
        user_name=current_user.nickname,
        phone=current_user.phone,
        destination=payload.destination.strip(),
        travel_time=payload.travel_time.strip(),
        days=payload.days.strip(),
        budget=payload.budget.strip(),
        people_count=payload.people_count.strip(),
        special_tags=_normalize_list(payload.special_tags),
        note=payload.note.strip(),
        status="pending",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/custom-travel/requests", response_model=list[CustomTravelRequestOut])
async def list_my_custom_travel_requests(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomTravelRequest]:
    """List custom travel requests submitted by the current user."""
    result = await db.scalars(
        select(CustomTravelRequest)
        .where(CustomTravelRequest.user_id == current_user.id)
        .order_by(desc(CustomTravelRequest.created_at))
    )
    return list(result)


@router.get("/custom-travel/requests/{request_id}", response_model=CustomTravelRequestOut)
async def get_my_custom_travel_request(
    request_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomTravelRequest:
    """Read one custom travel request owned by the current user."""
    item = await db.get(CustomTravelRequest, request_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定制需求不存在")
    return item


@router.get("/admin/custom-travel/requests", response_model=list[CustomTravelRequestOut])
async def admin_list_custom_travel_requests(
    _: UserAccount | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CustomTravelRequest]:
    """List all manual custom-travel requests for admin audit."""
    result = await db.scalars(select(CustomTravelRequest).order_by(desc(CustomTravelRequest.created_at)))
    return list(result)


@router.patch("/admin/custom-travel/requests/{request_id}/review", response_model=CustomTravelRequestOut)
async def admin_review_custom_travel_request(
    request_id: int,
    payload: CustomTravelReviewPayload,
    admin_user: UserAccount | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomTravelRequest:
    """Approve a custom request with a concrete plan or reject it with a reason."""
    item = await db.get(CustomTravelRequest, request_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定制需求不存在")

    if payload.approved:
        if not payload.plan_title.strip() or not payload.plan_summary.strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请填写方案标题和方案说明")
        item.status = "approved"
        item.reject_reason = ""
        item.plan_title = payload.plan_title.strip()
        item.plan_summary = payload.plan_summary.strip()
        item.plan_price = payload.plan_price.strip()
        item.plan_itinerary = _normalize_list(payload.plan_itinerary)
        item.plan_includes = _normalize_list(payload.plan_includes)
        item.plan_tips = payload.plan_tips.strip()
    else:
        item.status = "rejected"
        item.reject_reason = payload.reject_reason.strip() or "暂时无法匹配合适方案，请调整需求后重新提交"

    item.reviewed_at = datetime.now()
    item.reviewed_by = admin_user.id if admin_user else None
    await db.commit()
    await db.refresh(item)
    return item
