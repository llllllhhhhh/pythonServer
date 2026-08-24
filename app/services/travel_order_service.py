from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.order_status import TravelFulfillmentStatus, TravelOrderStatus
from app.models import (
    PointLedger,
    PointRule,
    TravelOrder,
    TravelOrderAuditLog,
    TravelRoute,
    TravelVerificationRecord,
    UserAccount,
)
from app.services.point_service import PointService


USER_CANCELLABLE_STATUSES = {
    TravelFulfillmentStatus.CONTRACT_PENDING,
    TravelFulfillmentStatus.CONTRACT_REVIEWING,
    TravelFulfillmentStatus.CONTRACT_REJECTED,
    TravelFulfillmentStatus.INFO_PENDING,
    TravelFulfillmentStatus.INFO_SUBMITTED,
}

NON_CANCELLABLE_STATUSES = {
    TravelFulfillmentStatus.CHECKED_IN,
    TravelFulfillmentStatus.IN_TRIP,
    TravelFulfillmentStatus.COMPLETED,
}


def qr_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def qr_expiry(travel_date: str) -> datetime:
    value = (travel_date or "").strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            departure = datetime.strptime(value, pattern)
            return max(datetime.now() + timedelta(hours=24), departure + timedelta(days=1))
        except ValueError:
            continue
    return datetime.now() + timedelta(days=3)


def add_travel_audit(
    db: AsyncSession,
    order: TravelOrder,
    *,
    event_type: str,
    from_status: str = "",
    to_status: str = "",
    actor: UserAccount | None = None,
    actor_role: str = "system",
    actor_name: str = "",
    reason: str = "",
    evidence: dict | None = None,
) -> TravelOrderAuditLog:
    item = TravelOrderAuditLog(
        order_id=order.id,
        order_no=order.order_no,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_role=actor.role if actor else actor_role,
        actor_id=actor.id if actor else 0,
        actor_name=(actor.nickname if actor else actor_name) or "",
        reason=reason,
        evidence=evidence or {},
    )
    db.add(item)
    return item


def add_verification_record(
    db: AsyncSession,
    order: TravelOrder,
    *,
    action: str,
    token: str,
    result: str,
    operator: UserAccount | None = None,
    device: str = "",
    location: str = "",
    reason: str = "",
) -> TravelVerificationRecord:
    item = TravelVerificationRecord(
        order_id=order.id,
        order_no=order.order_no,
        action=action,
        token_hash=qr_token_hash(token),
        result=result,
        operator_role=operator.role if operator else "system",
        operator_id=operator.id if operator else 0,
        operator_name=operator.nickname if operator else "",
        device=device[:255],
        location=location[:255],
        reason=reason[:255],
    )
    db.add(item)
    return item


async def cancel_travel_order(
    db: AsyncSession,
    order_id: int,
    *,
    actor: UserAccount,
    reason: str,
    admin_override: bool = False,
) -> TravelOrder:
    order = await db.scalar(select(TravelOrder).where(TravelOrder.id == order_id).with_for_update())
    if not order:
        raise BusinessError("旅行订单不存在", status_code=404, code="TRAVEL_ORDER_NOT_FOUND")
    if not admin_override and order.user_id != actor.id:
        raise BusinessError("旅行订单不存在", status_code=404, code="TRAVEL_ORDER_NOT_FOUND")
    if order.fulfillment_status == TravelFulfillmentStatus.CANCELLED:
        return order
    if order.fulfillment_status in NON_CANCELLABLE_STATUSES:
        raise BusinessError("订单已核销或行程已开始，不能取消", status_code=400, code="TRAVEL_ORDER_NOT_CANCELLABLE")
    if not admin_override and order.fulfillment_status not in USER_CANCELLABLE_STATUSES:
        raise BusinessError("接送安排确认后请联系平台申请取消", status_code=400, code="TRAVEL_ORDER_REQUIRES_ADMIN_CANCEL")

    previous_status = str(order.fulfillment_status or "")
    if order.order_type == "points_exchange" and int(order.points_cost or 0) > 0 and not order.points_refunded:
        user = await PointService.lock_user(db, order.user_id)
        rule = await db.get(PointRule, 1)
        fallback_expiry = datetime.now() + timedelta(days=max(1, int(rule.valid_days if rule else 365)))
        original = await db.scalar(
            select(PointLedger).where(
                PointLedger.user_id == user.id,
                PointLedger.biz_type == "travel_exchange",
                PointLedger.biz_no == order.order_no,
                PointLedger.direction == "expense",
            )
        )
        if original:
            await PointService.refund_locked(
                db,
                user,
                original_biz_type="travel_exchange",
                biz_no=order.order_no,
                refund_biz_type="travel_exchange_refund",
                idempotency_key=f"travel-exchange-refund:{order.order_no}",
                biz_id=order.id,
                remark="取消旅行兑换，退回积分",
                expired_lot_fallback=fallback_expiry,
            )
        else:
            await PointService.credit_locked(
                db,
                user,
                int(order.points_cost),
                biz_type="travel_exchange_refund",
                biz_id=order.id,
                biz_no=order.order_no,
                idempotency_key=f"travel-exchange-refund:{order.order_no}",
                remark="取消历史旅行兑换，退回积分",
                expires_at=fallback_expiry,
            )
        order.points_refunded = True

    if order.route_id and not order.stock_restored:
        route = await db.scalar(select(TravelRoute).where(TravelRoute.id == order.route_id).with_for_update())
        if route:
            route.stock = int(route.stock or 0) + 1
        order.stock_restored = True

    order.fulfillment_status = TravelFulfillmentStatus.CANCELLED
    order.status = int(TravelOrderStatus.CANCELLED)
    order.cancelled_at = datetime.now()
    order.cancel_reason = reason.strip()
    order.qr_token = ""
    order.qr_expires_at = None
    add_travel_audit(
        db,
        order,
        event_type="order_cancelled",
        from_status=previous_status,
        to_status=TravelFulfillmentStatus.CANCELLED,
        actor=actor,
        reason=reason,
        evidence={
            "points_refunded": bool(order.points_refunded),
            "stock_restored": bool(order.stock_restored),
        },
    )
    await db.commit()
    await db.refresh(order)
    return order
