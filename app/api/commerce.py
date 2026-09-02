import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.merchant import get_current_school
from app.core.cache import RedisUnavailableError, redis
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.core.exceptions import BusinessError
from app.core.order_status import CommercePaymentStatus, ReviewStatus
from app.models import CommerceOrder, LearningCheckinRecord, LearningProfile, PointRule, PreferenceEvent, SchoolSite, StudyContent, StudyOrder, StudyProduct, TravelRoute, UserAccount, UserEntitlement, WalletTransaction
from app.schemas.api import RouteCreate, RouteOut, RouteUpdate
from app.schemas.commerce import (
    LearningOnboardingUpdate,
    LearningProfileUpdate,
    StudyOrderCreate,
    StudyOrderOut,
    StudyProductPayload,
    StudyProductReviewPayload,
)
from app.schemas.order import OrderCreateRequest, OrderOut
from app.services.order_service import OrderService
from app.services.point_service import PointService
from app.services.wechat_pay_service import (
    create_jsapi_payment,
    decrypt_notification,
    is_wechat_pay_configured,
    verify_notification_signature,
    yuan_to_fen,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["study-commerce"])


async def commerce_order_out(db: AsyncSession, order: CommerceOrder) -> OrderOut:
    """Build a typed order response including order items.

    Args:
        db: SQLAlchemy async session.
        order: Order header model.

    Returns:
        Pydantic order response.
    """
    items = await OrderService.get_order_items(db, order.id)
    return OrderOut.model_validate({**order.__dict__, "items": items})


async def commerce_orders_out_batch(db: AsyncSession, orders: list[CommerceOrder]) -> list[OrderOut]:
    """Build order responses for many orders with a single items query."""
    items_map = await OrderService.get_order_items_map(db, [order.id for order in orders])
    return [
        OrderOut.model_validate({**order.__dict__, "items": items_map.get(order.id, [])})
        for order in orders
    ]


def product_dict(item: StudyProduct, contents: list[StudyContent] | None = None, owned: bool = False) -> dict:
    rows = [
        {
            "id": content.id,
            "product_id": content.product_id,
            "title": content.title,
            "content_type": content.content_type,
            "summary": content.summary,
            "resource_url": content.resource_url if owned or content.preview else "",
            "duration_minutes": content.duration_minutes,
            "preview": content.preview,
            "sort_order": content.sort_order,
            "status": content.status,
            "locked": not owned and not content.preview,
        }
        for content in (contents or [])
    ]
    return {
        "id": item.id,
        "school_id": item.school_id,
        "name": item.name,
        "product_type": item.product_type,
        "subtitle": item.subtitle,
        "description": item.description,
        "price": item.price,
        "original_price": item.original_price,
        "billing_cycle": item.billing_cycle,
        "cover": item.cover,
        "benefits": item.benefits or [],
        "trial_minutes": item.trial_minutes,
        "stock": item.stock,
        "sales": item.sales,
        "featured": item.featured,
        "installment_enabled": item.installment_enabled,
        "installment_count": item.installment_count,
        "status": item.status,
        "review_status": item.review_status,
        "reject_reason": item.reject_reason,
        "contents": rows,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "owned": owned,
    }


async def product_contents(db: AsyncSession, product_id: int, include_disabled: bool = False) -> list[StudyContent]:
    query = select(StudyContent).where(StudyContent.product_id == product_id)
    if not include_disabled:
        query = query.where(StudyContent.status.is_(True))
    return list(await db.scalars(query.order_by(StudyContent.sort_order, StudyContent.id)))


async def replace_contents(db: AsyncSession, product_id: int, rows: list) -> None:
    await db.execute(delete(StudyContent).where(StudyContent.product_id == product_id))
    for row in rows:
        db.add(StudyContent(product_id=product_id, **row.model_dump()))


async def get_point_rule(db: AsyncSession) -> PointRule:
    rule = await db.get(PointRule, 1)
    if not rule:
        rule = PointRule(id=1)
        db.add(rule)
        await db.flush()
    return rule


def point_expires_at(rule: PointRule) -> datetime:
    return datetime.now() + timedelta(days=max(1, int(rule.valid_days or 365)))


@router.get("/public/study/products")
async def public_products(product_type: str | None = Query(default=None), db: AsyncSession = Depends(get_db)):
    query = select(StudyProduct).where(
        StudyProduct.status.is_(True),
        StudyProduct.review_status == ReviewStatus.APPROVED,
    )
    if product_type:
        query = query.where(StudyProduct.product_type == product_type)
    products = list(await db.scalars(query.order_by(StudyProduct.featured.desc(), StudyProduct.id.desc())))
    return [product_dict(item) for item in products]


@router.get("/public/study/products/{product_id}")
async def public_product_detail(product_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item or not item.status or item.review_status != ReviewStatus.APPROVED:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return product_dict(item, await product_contents(db, product_id))


@router.post("/commerce/orders", response_model=StudyOrderOut, status_code=status.HTTP_201_CREATED)
async def create_study_order(
    payload: StudyOrderCreate,
    response: Response,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Legacy study-order path. New purchases should use the standard order
    # flow (/commerce/standard-orders) which supports Redis idempotency,
    # stock locking and balance payment.
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = "</api/v1/commerce/standard-orders>; rel=\"alternate\""
    product = await db.get(StudyProduct, payload.product_id)
    if not product or not product.status or product.review_status != ReviewStatus.APPROVED:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    if product.stock == 0:
        raise HTTPException(status_code=400, detail="商品库存不足")
    installments = payload.installment_count if product.installment_enabled else 1
    if installments > product.installment_count:
        raise HTTPException(status_code=400, detail="分期期数超过商品允许范围")
    price = Decimal(product.price)
    amount = (price / installments).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    if installments > 1 and amount * installments < price:
        # Push the rounding remainder into the final installment so the sum
        # of installments always equals the full price (no free orders).
        amount = (price - (amount * (installments - 1))).quantize(Decimal("0.01"))
    order = StudyOrder(
        order_no=f"ST{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}",
        user_id=user.id,
        school_id=product.school_id,
        product_id=product.id,
        product_name=product.name,
        product_type=product.product_type,
        amount=amount,
        payment_method=payload.payment_method,
        installment_count=installments,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/commerce/standard-orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_standard_order(
    payload: OrderCreateRequest,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Create a standard commerce order with Redis idempotency and stock lock."""
    order = await OrderService.create_order(db, user, payload)
    return await commerce_order_out(db, order)


@router.get("/commerce/standard-orders", response_model=list[OrderOut])
async def my_standard_orders(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[OrderOut]:
    """List current user's standard commerce orders."""
    orders = list(await db.scalars(select(CommerceOrder).where(CommerceOrder.user_id == user.id).order_by(CommerceOrder.id.desc())))
    return await commerce_orders_out_batch(db, orders)


@router.get("/commerce/standard-orders/{order_no}", response_model=OrderOut)
async def standard_order_detail(
    order_no: str,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await OrderService.get_order_by_no(db, order_no)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    return await commerce_order_out(db, order)


@router.get("/commerce/wallet")
async def my_wallet(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Return current user's wallet balance and recent transaction records."""
    transactions = list(
        await db.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user.id)
            .order_by(WalletTransaction.id.desc())
            .limit(50)
        )
    )
    return {
        "balance": user.balance,
        "transactions": [
            {
                "id": item.id,
                "transaction_no": item.transaction_no,
                "direction": item.direction,
                "amount": item.amount,
                "balance_before": item.balance_before,
                "balance_after": item.balance_after,
                "biz_type": item.biz_type,
                "biz_no": item.biz_no,
                "remark": item.remark,
                "created_at": item.created_at,
            }
            for item in transactions
        ],
    }


@router.post("/commerce/standard-orders/{order_no}/pay/balance", response_model=OrderOut)
async def balance_pay_standard_order(
    order_no: str,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Pay a standard order with the user's wallet balance."""
    order = await OrderService.get_order_by_no(db, order_no)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    paid = await OrderService.pay_with_balance(db, user, order)
    return await commerce_order_out(db, paid)


@router.post("/commerce/standard-orders/{order_no}/pay/wechat")
async def wechat_pay_standard_order(
    order_no: str,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await OrderService.get_order_by_no(db, order_no)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.payment_status == CommercePaymentStatus.PAID:
        raise BusinessError("订单已支付", status_code=409, code="ORDER_ALREADY_PAID")
    if order.status != "pending":
        raise BusinessError("订单当前状态不可支付", status_code=400, code="ORDER_NOT_PAYABLE")
    items = await OrderService.get_order_items(db, order.id)
    description = "、".join(item.product_name for item in items) or "学徒行学习服务"
    params = await create_jsapi_payment(order, description, user.wechat_openid or "")
    if order.payment_method != "wechat":
        order.payment_method = "wechat"
        await db.commit()
    return {"order_no": order.order_no, "amount": order.payable_amount, **params}


@router.post("/commerce/wechat/notify")
async def wechat_pay_notification(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    verify_notification_signature(request.headers, body)
    transaction = decrypt_notification(body)
    if transaction.get("trade_state") != "SUCCESS":
        return {"code": "SUCCESS", "message": "成功"}
    if transaction.get("mchid") != settings.wechat_mch_id:
        raise BusinessError("微信支付商户号不匹配", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    if transaction.get("appid") != settings.wechat_mini_app_id:
        raise BusinessError("微信支付 AppID 不匹配", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    order_no = str(transaction.get("out_trade_no") or "")
    order = await OrderService.get_order_by_no_for_update(db, order_no)
    if not order:
        raise BusinessError("微信支付订单不存在", status_code=404, code="ORDER_NOT_FOUND")
    amount = transaction.get("amount") or {}
    try:
        callback_total = int(amount.get("total", -1))
    except (TypeError, ValueError) as exc:
        raise BusinessError("微信支付回调金额格式错误", status_code=400, code="WECHAT_PAY_AMOUNT_MISMATCH") from exc
    if amount.get("currency", "CNY") != "CNY" or callback_total != yuan_to_fen(order.payable_amount):
        raise BusinessError("微信支付回调金额不匹配", status_code=400, code="WECHAT_PAY_AMOUNT_MISMATCH")
    transaction_id = str(transaction.get("transaction_id") or "")
    if not transaction_id:
        raise BusinessError("微信支付交易号缺失", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    await OrderService.mark_order_paid(db, order, payment_method="wechat", transaction_id=transaction_id)
    return {"code": "SUCCESS", "message": "成功"}


@router.post("/commerce/standard-orders/{order_no}/pay/mock", response_model=OrderOut)
async def mock_pay_standard_order(
    order_no: str,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Mock pay a standard order in development/test environments."""
    if settings.environment.strip().lower() not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="生产环境不允许模拟支付")
    order = await OrderService.get_order_by_no(db, order_no)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    paid = await OrderService.mark_order_paid(db, order, payment_method="mock", transaction_id=f"MOCK-{uuid4().hex.upper()}")
    return await commerce_order_out(db, paid)


async def grant_entitlement(db: AsyncSession, order: StudyOrder, product: StudyProduct) -> None:
    if await db.scalar(select(UserEntitlement).where(UserEntitlement.order_id == order.id)):
        return
    expires_at = None
    if product.billing_cycle == "month":
        expires_at = datetime.now() + timedelta(days=30)
    elif product.billing_cycle == "year":
        expires_at = datetime.now() + timedelta(days=365)
    db.add(
        UserEntitlement(
            user_id=order.user_id,
            product_id=order.product_id,
            order_id=order.id,
            entitlement_type=product.product_type,
            expires_at=expires_at,
        )
    )
    if not await db.scalar(select(LearningProfile).where(LearningProfile.user_id == order.user_id)):
        db.add(LearningProfile(user_id=order.user_id))
    product.sales += 1
    if product.stock > 0:
        product.stock -= 1


async def grant_study_purchase_points(db: AsyncSession, order: StudyOrder) -> None:
    rule = await get_point_rule(db)
    amount = int(rule.purchase_score or 0)
    if not rule.enabled or not rule.purchase_enabled or amount <= 0:
        return
    user = await PointService.lock_user(db, order.user_id)
    await PointService.credit_locked(
        db,
        user,
        amount,
        biz_type="study_purchase",
        biz_id=order.id,
        biz_no=order.order_no,
        idempotency_key=f"study-purchase:{order.id}",
        remark=f"购买课程 {order.product_name} 奖励",
        expires_at=point_expires_at(rule),
    )


@router.post("/commerce/orders/{order_id}/pay/mock", response_model=StudyOrderOut)
async def mock_pay(order_id: int, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if settings.environment.strip().lower() not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="生产环境不允许模拟支付")
    order = await db.scalar(select(StudyOrder).where(StudyOrder.id == order_id).with_for_update())
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.payment_status == CommercePaymentStatus.PAID:
        return order
    product = await db.scalar(select(StudyProduct).where(StudyProduct.id == order.product_id).with_for_update())
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    order.payment_status, order.payment_method = CommercePaymentStatus.PAID, "mock"
    order.transaction_id, order.paid_at = f"MOCK-{uuid4().hex.upper()}", datetime.now()
    await grant_entitlement(db, order, product)
    await grant_study_purchase_points(db, order)
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/commerce/orders/{order_id}/payment")
async def payment_params(order_id: int, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(StudyOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    configured = is_wechat_pay_configured()
    return {
        "order_no": order.order_no,
        "amount": order.amount,
        "provider": "wechat",
        "configured": configured,
        "message": "微信支付参数已就绪" if configured else "请在服务端 .env 配置微信支付商户参数",
    }


@router.get("/commerce/orders", response_model=list[StudyOrderOut])
async def my_study_orders(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return list(await db.scalars(select(StudyOrder).where(StudyOrder.user_id == user.id).order_by(StudyOrder.id.desc())))


async def ensure_learning_checkin_record(
    db: AsyncSession,
    user_id: int,
    checkin_at: datetime,
    checkin_days: int,
) -> LearningCheckinRecord:
    checkin_date = checkin_at.date().isoformat()
    existing = await db.scalar(
        select(LearningCheckinRecord).where(
            LearningCheckinRecord.user_id == user_id,
            LearningCheckinRecord.checkin_date == checkin_date,
        )
    )
    if existing:
        return existing
    record = LearningCheckinRecord(
        user_id=user_id,
        checkin_date=checkin_date,
        checkin_at=checkin_at,
        checkin_days=checkin_days or 1,
        note="今日已完成学习签到",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.get("/commerce/me/learning-center")
async def learning_center(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entitlements = list(await db.scalars(select(UserEntitlement).where(UserEntitlement.user_id == user.id).order_by(UserEntitlement.id.desc())))
    product_ids = {row.product_id for row in entitlements}
    products = {}
    if product_ids:
        products = {item.id: item for item in list(await db.scalars(select(StudyProduct).where(StudyProduct.id.in_(product_ids))))}
    profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    today = datetime.now().date().isoformat()
    checkin_records = list(
        await db.scalars(
            select(LearningCheckinRecord)
            .where(LearningCheckinRecord.user_id == user.id)
            .order_by(LearningCheckinRecord.checkin_at.desc(), LearningCheckinRecord.id.desc())
            .limit(60)
        )
    )
    if profile.last_checkin_at and not any(item.checkin_date == profile.last_checkin_at.date().isoformat() for item in checkin_records):
        record = await ensure_learning_checkin_record(db, user.id, profile.last_checkin_at, profile.checkin_days)
        checkin_records.insert(0, record)
    if profile.last_checkin_at and not any(item.checkin_date == profile.last_checkin_at.date().isoformat() for item in checkin_records):
        checkin_records.insert(
            0,
            LearningCheckinRecord(
                id=0,
                user_id=user.id,
                checkin_date=profile.last_checkin_at.date().isoformat(),
                checkin_at=profile.last_checkin_at,
                checkin_days=profile.checkin_days or 1,
                note="今日已完成学习打卡",
            ),
        )
    rights = []
    for row in entitlements:
        product = products.get(row.product_id)
        if product:
            rights.append(
                {
                    "id": row.id,
                    "product_id": row.product_id,
                    "product_name": product.name,
                    "product_type": product.product_type,
                    "starts_at": row.starts_at,
                    "expires_at": row.expires_at,
                    "progress": row.progress,
                    "status": row.status,
                    "product": product_dict(product, await product_contents(db, product.id), owned=True),
                }
            )
    return {
        "profile": {
            "target_exam": profile.target_exam,
            "current_stage": profile.current_stage,
            "school_major_status": profile.school_major_status,
            "interest_tags": profile.interest_tags or [],
            "onboarding_version": profile.onboarding_version,
            "onboarding_answers": profile.onboarding_answers or {},
            "onboarding_completed_at": profile.onboarding_completed_at,
            "total_minutes": profile.total_minutes,
            "checkin_days": profile.checkin_days,
            "last_checkin_at": profile.last_checkin_at,
            "checked_today": any(item.checkin_date == today for item in checkin_records)
            or bool(profile.last_checkin_at and profile.last_checkin_at.date().isoformat() == today),
            "advisor_notes": profile.advisor_notes,
        },
        "checkin_records": [
            {
                "id": item.id,
                "checkin_date": item.checkin_date,
                "checkin_at": item.checkin_at,
                "checkin_days": item.checkin_days,
                "note": item.note,
            }
            for item in checkin_records
        ],
        "entitlements": rights,
    }


@router.put("/commerce/me/learning-profile")
async def update_learning_profile(payload: LearningProfileUpdate, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
    profile.target_exam, profile.current_stage = payload.target_exam, payload.current_stage
    await db.commit()
    return {"ok": True}


def _answer_text(answers: dict, key: str) -> str:
    value = answers.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _answer_list(answers: dict, key: str) -> list[str]:
    value = answers.get(key, [])
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


@router.put("/commerce/me/onboarding")
async def complete_learning_onboarding(
    payload: LearningOnboardingUpdate,
    user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
        await db.flush()

    if payload.version < profile.onboarding_version:
        return {"ok": True, "version": profile.onboarding_version, "ignored": True}
    if payload.version == profile.onboarding_version and profile.onboarding_completed_at:
        return {"ok": True, "version": profile.onboarding_version, "ignored": False}

    answers = payload.answers
    stage = _answer_text(answers, "stage")
    target = _answer_text(answers, "target")
    school_major_status = _answer_text(answers, "schoolMajorStatus")
    interests = _answer_list(answers, "interests")

    if stage:
        profile.current_stage = stage[:80]
    if target:
        profile.target_exam = target[:80]
    profile.school_major_status = school_major_status[:80]
    profile.interest_tags = interests
    profile.onboarding_version = payload.version
    profile.onboarding_answers = answers
    profile.onboarding_completed_at = datetime.now()

    for interest in interests:
        db.add(
            PreferenceEvent(
                user_id=str(user.id),
                user_name=user.nickname,
                preference_type="study",
                target_key=interest[:120],
                target_name=interest[:160],
                action="onboarding_select",
                score=5,
            )
        )

    await db.commit()
    return {"ok": True, "version": profile.onboarding_version, "ignored": False}


@router.post("/commerce/me/check-in")
async def study_check_in(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(
        select(LearningProfile).where(LearningProfile.user_id == user.id).with_for_update()
    )
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
        await db.flush()
    now = datetime.now()
    today = now.date().isoformat()
    existing = await db.scalar(
        select(LearningCheckinRecord).where(
            LearningCheckinRecord.user_id == user.id,
            LearningCheckinRecord.checkin_date == today,
        )
    )
    if existing or (profile.last_checkin_at and profile.last_checkin_at.date() == now.date()):
        if not existing and profile.last_checkin_at:
            existing = await ensure_learning_checkin_record(db, user.id, profile.last_checkin_at, profile.checkin_days)
        record_time = existing.checkin_at if existing else profile.last_checkin_at
        return {
            "checked": True,
            "checkin_days": profile.checkin_days,
            "message": "今天已经打卡",
            "record": {
                "id": existing.id if existing else 0,
                "checkin_date": today,
                "checkin_at": record_time,
                "checkin_days": profile.checkin_days,
                "note": (existing.note if existing else "") or "今日已完成学习打卡",
            },
        }
    profile.checkin_days += 1
    profile.last_checkin_at = now
    record = LearningCheckinRecord(
        user_id=user.id,
        checkin_date=today,
        checkin_at=now,
        checkin_days=profile.checkin_days,
        note="今日已完成学习打卡",
    )
    db.add(record)
    rule = await get_point_rule(db)
    if rule.enabled and rule.checkin_enabled and int(rule.checkin_score or 0) > 0:
        locked_user = await PointService.lock_user(db, user.id)
        await PointService.credit_locked(
            db,
            locked_user,
            int(rule.checkin_score),
            biz_type="learning_checkin",
            biz_id=0,
            biz_no=today,
            idempotency_key=f"learning-checkin:{user.id}:{today}",
            remark="学习签到奖励",
            expires_at=point_expires_at(rule),
        )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(LearningCheckinRecord).where(
                LearningCheckinRecord.user_id == user.id,
                LearningCheckinRecord.checkin_date == today,
            )
        )
        profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
        if not existing or not profile:
            raise
        return {
            "checked": True,
            "checkin_days": profile.checkin_days,
            "message": "今天已经打卡",
            "record": {
                "id": existing.id,
                "checkin_date": existing.checkin_date,
                "checkin_at": existing.checkin_at,
                "checkin_days": existing.checkin_days,
                "note": existing.note or "今日已完成学习打卡",
            },
        }
    await db.refresh(record)
    return {
        "checked": True,
        "checkin_days": profile.checkin_days,
        "message": "打卡成功",
        "record": {
            "id": record.id,
            "checkin_date": record.checkin_date,
            "checkin_at": record.checkin_at,
            "checkin_days": record.checkin_days,
            "note": record.note,
        },
    }


@router.get("/admin/study/products", dependencies=[Depends(require_admin)])
async def admin_products(db: AsyncSession = Depends(get_db)):
    products = list(await db.scalars(select(StudyProduct).order_by(StudyProduct.id.desc())))
    return [product_dict(item, await product_contents(db, item.id, True)) for item in products]


@router.post("/admin/study/products", dependencies=[Depends(require_admin)], status_code=201)
async def create_product(payload: StudyProductPayload, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump(exclude={"contents", "review_status", "reject_reason"})
    data["review_status"] = ReviewStatus.APPROVED
    data["reject_reason"] = ""
    item = StudyProduct(**data)
    db.add(item)
    await db.flush()
    await replace_contents(db, item.id, payload.contents)
    await db.commit()
    await db.refresh(item)
    return product_dict(item, await product_contents(db, item.id, True))


@router.put("/admin/study/products/{product_id}", dependencies=[Depends(require_admin)])
async def update_product(product_id: int, payload: StudyProductPayload, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = payload.model_dump(exclude={"contents", "review_status", "reject_reason"})
    if item.review_status != ReviewStatus.APPROVED:
        data["status"] = False
    for key, value in data.items():
        setattr(item, key, value)
    await replace_contents(db, item.id, payload.contents)
    await db.commit()
    await db.refresh(item)
    if item.stock >= 0:
        try:
            await redis.sync_stock(item.id, item.stock)
        except Exception:  # stock sync is best-effort; never fail the main write
            logger.exception("admin_sync_stock_failed", extra={"product_id": item.id})
    return product_dict(item, await product_contents(db, item.id, True))


@router.patch("/admin/study/products/{product_id}/review", dependencies=[Depends(require_admin)])
async def review_product(product_id: int, payload: StudyProductReviewPayload, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    item.review_status = ReviewStatus.APPROVED if payload.approved else ReviewStatus.REJECTED
    item.reject_reason = "" if payload.approved else payload.reject_reason
    item.status = bool(payload.approved)
    await db.commit()
    await db.refresh(item)
    return product_dict(item, await product_contents(db, item.id, True))


@router.patch("/admin/study/products/{product_id}/status", dependencies=[Depends(require_admin)])
async def set_product_status(product_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    if enabled and item.review_status != ReviewStatus.APPROVED:
        raise HTTPException(status_code=400, detail="产品需要先审核通过")
    item.status = enabled
    await db.commit()
    return {"id": item.id, "status": item.status}


@router.get("/admin/study/orders", response_model=list[StudyOrderOut], dependencies=[Depends(require_admin)])
async def admin_study_orders(db: AsyncSession = Depends(get_db)):
    return list(await db.scalars(select(StudyOrder).order_by(StudyOrder.id.desc())))


@router.get("/admin/commerce/orders", response_model=list[OrderOut], dependencies=[Depends(require_admin)])
async def admin_commerce_orders(db: AsyncSession = Depends(get_db)) -> list[OrderOut]:
    """List all standard commerce orders for platform administrators."""
    orders = list(await db.scalars(select(CommerceOrder).order_by(CommerceOrder.id.desc())))
    return await commerce_orders_out_batch(db, orders)


@router.get("/merchant/study/products")
async def merchant_products(school: SchoolSite = Depends(get_current_school), db: AsyncSession = Depends(get_db)):
    products = list(
        await db.scalars(
            select(StudyProduct)
            .where(StudyProduct.school_id == school.id)
            .order_by(StudyProduct.id.desc())
        )
    )
    return [product_dict(item, await product_contents(db, item.id, True)) for item in products]


@router.post("/merchant/study/products", status_code=201)
async def merchant_create_product(
    payload: StudyProductPayload,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"contents"})
    data["school_id"] = school.id
    data["review_status"] = ReviewStatus.PENDING
    data["reject_reason"] = ""
    data["status"] = False
    item = StudyProduct(**data)
    db.add(item)
    await db.flush()
    await replace_contents(db, item.id, payload.contents)
    await db.commit()
    await db.refresh(item)
    return product_dict(item, await product_contents(db, item.id, True))


@router.put("/merchant/study/products/{product_id}")
async def merchant_update_product(
    product_id: int,
    payload: StudyProductPayload,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(StudyProduct, product_id)
    if not item or item.school_id != school.id:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = payload.model_dump(exclude={"contents"})
    data["school_id"] = school.id
    data["review_status"] = ReviewStatus.PENDING
    data["reject_reason"] = ""
    data["status"] = False
    for key, value in data.items():
        setattr(item, key, value)
    await replace_contents(db, item.id, payload.contents)
    await db.commit()
    await db.refresh(item)
    if item.stock >= 0:
        try:
            await redis.sync_stock(item.id, item.stock)
        except Exception:  # stock sync is best-effort; never fail the main write
            logger.exception("merchant_sync_stock_failed", extra={"product_id": item.id})
    return product_dict(item, await product_contents(db, item.id, True))


@router.patch("/merchant/study/products/{product_id}/status")
async def merchant_set_product_status(
    product_id: int,
    enabled: bool,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(StudyProduct, product_id)
    if not item or item.school_id != school.id:
        raise HTTPException(status_code=404, detail="商品不存在")
    if enabled and item.review_status != ReviewStatus.APPROVED:
        raise HTTPException(status_code=400, detail="产品需要先审核通过")
    item.status = enabled
    await db.commit()
    await db.refresh(item)
    return product_dict(item, await product_contents(db, item.id, True))


@router.get("/merchant/travel/routes", response_model=list[RouteOut])
async def merchant_travel_routes(
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    return list(
        await db.scalars(
            select(TravelRoute)
            .where(TravelRoute.school_id == school.id)
            .order_by(TravelRoute.id.desc())
        )
    )


@router.post("/merchant/travel/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def merchant_create_travel_route(
    payload: RouteCreate,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    data["school_id"] = school.id
    data["agency"] = data.get("agency") or school.name
    data["review_status"] = ReviewStatus.PENDING
    data["reject_reason"] = ""
    data["status"] = False
    item = TravelRoute(**data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/merchant/travel/routes/{route_id}", response_model=RouteOut)
async def merchant_update_travel_route(
    route_id: int,
    payload: RouteUpdate,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelRoute, route_id)
    if not item or item.school_id != school.id:
        raise HTTPException(status_code=404, detail="Route not found")
    data = payload.model_dump()
    data["school_id"] = school.id
    data["agency"] = data.get("agency") or school.name
    data["review_status"] = ReviewStatus.PENDING
    data["reject_reason"] = ""
    data["status"] = False
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/merchant/travel/routes/{route_id}/status", response_model=RouteOut)
async def merchant_set_travel_route_status(
    route_id: int,
    enabled: bool,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(TravelRoute, route_id)
    if not item or item.school_id != school.id:
        raise HTTPException(status_code=404, detail="Route not found")
    if enabled and item.review_status != ReviewStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Route needs platform approval first")
    item.status = enabled
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/merchant/study/orders", response_model=list[StudyOrderOut])
async def merchant_study_orders(school: SchoolSite = Depends(get_current_school), db: AsyncSession = Depends(get_db)):
    return list(
        await db.scalars(
            select(StudyOrder)
            .where(StudyOrder.school_id == school.id)
            .order_by(StudyOrder.id.desc())
        )
    )


@router.get("/merchant/commerce/orders", response_model=list[OrderOut])
async def merchant_commerce_orders(school: SchoolSite = Depends(get_current_school), db: AsyncSession = Depends(get_db)) -> list[OrderOut]:
    """List standard commerce orders that contain products of current school."""
    orders = list(
        await db.scalars(
            select(CommerceOrder)
            .where(CommerceOrder.school_id.in_([0, school.id]))
            .order_by(CommerceOrder.id.desc())
        )
    )
    items_map = await OrderService.get_order_items_map(db, [order.id for order in orders])
    result: list[OrderOut] = []
    for order in orders:
        items = items_map.get(order.id, [])
        if any(item.school_id == school.id for item in items):
            result.append(OrderOut.model_validate({**order.__dict__, "items": items}))
    return result
