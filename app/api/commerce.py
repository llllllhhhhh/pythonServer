from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models import LearningProfile, StudyContent, StudyOrder, StudyProduct, UserAccount, UserEntitlement
from app.schemas.commerce import LearningProfileUpdate, StudyOrderCreate, StudyOrderOut, StudyProductPayload

router = APIRouter(tags=["study-commerce"])


def product_dict(item: StudyProduct, contents: list[StudyContent] | None = None, owned: bool = False) -> dict:
    rows = [{
        "id": content.id, "product_id": content.product_id, "title": content.title,
        "content_type": content.content_type, "summary": content.summary,
        "resource_url": content.resource_url if owned or content.preview else "",
        "duration_minutes": content.duration_minutes, "preview": content.preview,
        "sort_order": content.sort_order, "status": content.status,
        "locked": not owned and not content.preview,
    } for content in (contents or [])]
    return {
        "id": item.id, "name": item.name, "product_type": item.product_type,
        "subtitle": item.subtitle, "description": item.description,
        "price": item.price, "original_price": item.original_price,
        "billing_cycle": item.billing_cycle, "cover": item.cover,
        "benefits": item.benefits or [], "trial_minutes": item.trial_minutes,
        "stock": item.stock, "sales": item.sales, "featured": item.featured,
        "installment_enabled": item.installment_enabled, "installment_count": item.installment_count,
        "status": item.status, "contents": rows, "created_at": item.created_at,
        "updated_at": item.updated_at, "owned": owned,
    }


async def product_contents(db: AsyncSession, product_id: int, include_disabled: bool = False) -> list[StudyContent]:
    query = select(StudyContent).where(StudyContent.product_id == product_id)
    if not include_disabled:
        query = query.where(StudyContent.status.is_(True))
    return list(await db.scalars(query.order_by(StudyContent.sort_order, StudyContent.id)))


@router.get("/public/study/products")
async def public_products(product_type: str | None = Query(default=None), db: AsyncSession = Depends(get_db)):
    query = select(StudyProduct).where(StudyProduct.status.is_(True))
    if product_type:
        query = query.where(StudyProduct.product_type == product_type)
    products = list(await db.scalars(query.order_by(StudyProduct.featured.desc(), StudyProduct.id.desc())))
    return [product_dict(item) for item in products]


@router.get("/public/study/products/{product_id}")
async def public_product_detail(product_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item or not item.status:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return product_dict(item, await product_contents(db, product_id))


@router.post("/commerce/orders", response_model=StudyOrderOut, status_code=status.HTTP_201_CREATED)
async def create_study_order(payload: StudyOrderCreate, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    product = await db.get(StudyProduct, payload.product_id)
    if not product or not product.status:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    if product.stock == 0:
        raise HTTPException(status_code=400, detail="商品库存不足")
    installments = payload.installment_count if product.installment_enabled else 1
    if installments > product.installment_count:
        raise HTTPException(status_code=400, detail="分期期数超过商品允许范围")
    amount = (Decimal(product.price) / installments).quantize(Decimal("0.01"))
    order = StudyOrder(
        order_no=f"ST{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}",
        user_id=user.id, product_id=product.id, product_name=product.name,
        product_type=product.product_type, amount=amount,
        payment_method=payload.payment_method, installment_count=installments,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def grant_entitlement(db: AsyncSession, order: StudyOrder, product: StudyProduct) -> None:
    if await db.scalar(select(UserEntitlement).where(UserEntitlement.order_id == order.id)):
        return
    expires_at = None
    if product.billing_cycle == "month":
        expires_at = datetime.now() + timedelta(days=30)
    elif product.billing_cycle == "year":
        expires_at = datetime.now() + timedelta(days=365)
    db.add(UserEntitlement(user_id=order.user_id, product_id=order.product_id, order_id=order.id,
                           entitlement_type=product.product_type, expires_at=expires_at))
    if not await db.scalar(select(LearningProfile).where(LearningProfile.user_id == order.user_id)):
        db.add(LearningProfile(user_id=order.user_id))
    product.sales += 1
    if product.stock > 0:
        product.stock -= 1


@router.post("/commerce/orders/{order_id}/pay/mock", response_model=StudyOrderOut)
async def mock_pay(order_id: int, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if settings.environment not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="生产环境不允许模拟支付")
    order = await db.get(StudyOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.payment_status == "paid":
        return order
    product = await db.get(StudyProduct, order.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    order.payment_status, order.payment_method = "paid", "mock"
    order.transaction_id, order.paid_at = f"MOCK-{uuid4().hex.upper()}", datetime.now()
    await grant_entitlement(db, order, product)
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/commerce/orders/{order_id}/payment")
async def payment_params(order_id: int, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(StudyOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    configured = bool(settings.wechat_app_id and settings.wechat_mch_id and settings.wechat_api_v3_key)
    return {"order_no": order.order_no, "amount": order.amount, "provider": "wechat",
            "configured": configured, "message": "微信支付参数已就绪" if configured else "请在服务端 .env 配置微信支付商户参数"}


@router.get("/commerce/orders", response_model=list[StudyOrderOut])
async def my_study_orders(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return list(await db.scalars(select(StudyOrder).where(StudyOrder.user_id == user.id).order_by(StudyOrder.id.desc())))


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
    rights = []
    for row in entitlements:
        product = products.get(row.product_id)
        if product:
            rights.append({"id": row.id, "product_id": row.product_id, "product_name": product.name,
                           "product_type": product.product_type, "starts_at": row.starts_at,
                           "expires_at": row.expires_at, "progress": row.progress, "status": row.status,
                           "product": product_dict(product, await product_contents(db, product.id), owned=True)})
    return {"profile": {"target_exam": profile.target_exam, "current_stage": profile.current_stage,
                         "total_minutes": profile.total_minutes, "checkin_days": profile.checkin_days,
                         "last_checkin_at": profile.last_checkin_at, "advisor_notes": profile.advisor_notes},
            "entitlements": rights}


@router.put("/commerce/me/learning-profile")
async def update_learning_profile(payload: LearningProfileUpdate, user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
    profile.target_exam, profile.current_stage = payload.target_exam, payload.current_stage
    await db.commit()
    return {"ok": True}


@router.post("/commerce/me/check-in")
async def study_check_in(user: UserAccount = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await db.scalar(select(LearningProfile).where(LearningProfile.user_id == user.id))
    if not profile:
        profile = LearningProfile(user_id=user.id)
        db.add(profile)
    if profile.last_checkin_at and profile.last_checkin_at.date() == datetime.now().date():
        return {"checked": True, "checkin_days": profile.checkin_days, "message": "今天已经打卡"}
    profile.checkin_days += 1
    profile.last_checkin_at = datetime.now()
    await db.commit()
    return {"checked": True, "checkin_days": profile.checkin_days, "message": "打卡成功"}


@router.get("/admin/study/products", dependencies=[Depends(require_admin)])
async def admin_products(db: AsyncSession = Depends(get_db)):
    products = list(await db.scalars(select(StudyProduct).order_by(StudyProduct.id.desc())))
    return [product_dict(item, await product_contents(db, item.id, True)) for item in products]


async def replace_contents(db: AsyncSession, product_id: int, rows: list) -> None:
    await db.execute(delete(StudyContent).where(StudyContent.product_id == product_id))
    for row in rows:
        db.add(StudyContent(product_id=product_id, **row.model_dump()))


@router.post("/admin/study/products", dependencies=[Depends(require_admin)], status_code=201)
async def create_product(payload: StudyProductPayload, db: AsyncSession = Depends(get_db)):
    item = StudyProduct(**payload.model_dump(exclude={"contents"}))
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
    for key, value in payload.model_dump(exclude={"contents"}).items():
        setattr(item, key, value)
    await replace_contents(db, item.id, payload.contents)
    await db.commit()
    await db.refresh(item)
    return product_dict(item, await product_contents(db, item.id, True))


@router.patch("/admin/study/products/{product_id}/status", dependencies=[Depends(require_admin)])
async def set_product_status(product_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(StudyProduct, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    item.status = enabled
    await db.commit()
    return {"id": item.id, "status": item.status}


@router.get("/admin/study/orders", response_model=list[StudyOrderOut], dependencies=[Depends(require_admin)])
async def admin_study_orders(db: AsyncSession = Depends(get_db)):
    return list(await db.scalars(select(StudyOrder).order_by(StudyOrder.id.desc())))
