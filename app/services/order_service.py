import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import RedisUnavailableError, redis
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import IdempotencyConflictError, InsufficientStockError, RedisRequiredError, BusinessError
from app.core.order_status import CommerceOrderStatus, CommercePaymentStatus, ReviewStatus
from app.core.snowflake import SnowflakeGenerator
from app.core.vip import get_vip_plan
from app.models import (
    CommerceOrder,
    CommerceOrderItem,
    LearningProfile,
    PointRule,
    StudyProduct,
    UserAccount,
    UserEntitlement,
    WalletTransaction,
)
from app.schemas.order import OrderCreateRequest
from app.tasks.order_tasks import enqueue_order_payment_timeout
from app.services.point_service import PointService

logger = logging.getLogger(__name__)
snowflake = SnowflakeGenerator(settings.snowflake_worker_id, settings.snowflake_datacenter_id)


class OrderService:
    """Business service for commerce order lifecycle."""

    @classmethod
    async def create_order(cls, db: AsyncSession, user: UserAccount, payload: OrderCreateRequest) -> CommerceOrder:
        """Create an order with Redis idempotency and stock pre-deduction.

        The flow is:
        1. Use Redis SETNX to reserve the idempotency key.
        2. Validate products from MySQL.
        3. Use Redis DECRBY to pre-deduct finite stock.
        4. Roll back all Redis deductions when any product is insufficient.
        5. Persist order header and item rows.
        6. Enqueue an RQ delayed job to cancel unpaid orders after 30 minutes.

        Args:
            db: SQLAlchemy async session.
            user: Current user.
            payload: Validated order creation request.

        Returns:
            The created `CommerceOrder`.

        Raises:
            IdempotencyConflictError: If the request is submitted repeatedly.
            InsufficientStockError: If any product has insufficient stock.
            RedisRequiredError: If Redis is disabled or unavailable.
            BusinessError: For invalid products.
        """
        idem_key = f"idem:order:{user.id}:{payload.idempotency_key}"
        result_key = f"{idem_key}:result"
        existing_order = await cls.get_order_by_idempotency_key(db, user.id, payload.idempotency_key)
        if existing_order:
            logger.info("order_idempotent_db_hit", extra={"user_id": user.id, "order_no": existing_order.order_no})
            return existing_order

        redis_locked = False
        try:
            existing = await redis.get_json(result_key)
            if existing and existing.get("order_no"):
                logger.info("order_idempotent_result_hit", extra={"user_id": user.id, "order_no": existing["order_no"]})
                order = await cls.get_order_by_no(db, existing["order_no"])
                if order:
                    return order

            redis_locked = await redis.setnx(idem_key, "processing", settings.order_idempotency_ttl_seconds)
            if not redis_locked:
                raise IdempotencyConflictError()
        except RedisUnavailableError as exc:
            logger.exception("order_create_redis_required", extra={"user_id": user.id})
            raise RedisRequiredError() from exc

        deducted: list[tuple[int, int]] = []
        try:
            quantity_map = cls._merge_items(payload)
            products = await cls._load_products(db, quantity_map)
            cls._validate_products(products, quantity_map)

            if redis_locked:
                await cls._pre_deduct_stock(products, quantity_map, deducted)
            try:
                order = await cls._persist_order(db, user, payload, products, quantity_map)
            except IntegrityError as exc:
                await db.rollback()
                if redis_locked:
                    await cls._rollback_redis_stock(deducted)
                    try:
                        await redis.delete(idem_key)
                    except RedisUnavailableError:
                        logger.exception("order_idempotency_unlock_failed", extra={"key": idem_key})
                existing_order = await cls.get_order_by_idempotency_key(db, user.id, payload.idempotency_key)
                if existing_order:
                    logger.info("order_idempotent_unique_hit", extra={"user_id": user.id, "order_no": existing_order.order_no})
                    return existing_order
                raise exc

            if redis_locked:
                try:
                    await redis.set_json(result_key, {"order_no": order.order_no}, settings.order_idempotency_ttl_seconds)
                except Exception:  # pragma: no cover - Redis result cache should not invalidate committed orders.
                    logger.exception("order_idempotency_result_cache_failed", extra={"order_no": order.order_no})
            enqueue_order_payment_timeout(order.order_no)
            logger.info("order_created", extra={"order_no": order.order_no, "user_id": user.id, "amount": str(order.payable_amount)})
            return order
        except Exception:
            await db.rollback()
            if redis_locked:
                await cls._rollback_redis_stock(deducted)
                try:
                    await redis.delete(idem_key)
                except RedisUnavailableError:
                    logger.exception("order_idempotency_unlock_failed", extra={"key": idem_key})
            logger.exception("order_create_failed", extra={"user_id": user.id, "idempotency_key": payload.idempotency_key})
            raise

    @classmethod
    async def mark_order_paid(cls, db: AsyncSession, order: CommerceOrder, payment_method: str = "balance", transaction_id: str = "") -> CommerceOrder:
        """Mark a pending order as paid and grant user entitlements.

        Args:
            db: SQLAlchemy async session.
            order: Order to pay.
            payment_method: Payment method.
            transaction_id: External payment transaction id.

        Returns:
            Paid order.
        """
        # Every payment entry point must serialize on the order row. The balance
        # path already locks before calling this method, while webhook/mock paths
        # may arrive concurrently with the same pending order.
        locked_order = await db.scalar(
            select(CommerceOrder).where(CommerceOrder.id == order.id).with_for_update()
        )
        if not locked_order:
            raise BusinessError("订单不存在", status_code=404, code="ORDER_NOT_FOUND")
        order = locked_order
        # Idempotent replay of an already-paid order is the normal case for
        # retries; only inconsistent rows (paid + canceled, or non-pending
        # statuses) are rejected.
        if order.payment_status == CommercePaymentStatus.PAID and order.status == CommerceOrderStatus.PAID:
            return order
        if order.status != CommerceOrderStatus.PENDING:
            raise BusinessError("订单当前状态不可支付", status_code=400, code="ORDER_NOT_PAYABLE")
        if order.payment_status == CommercePaymentStatus.PAID:
            raise BusinessError("订单当前状态不可支付", status_code=400, code="ORDER_NOT_PAYABLE")

        order.payment_status = CommercePaymentStatus.PAID
        order.status = CommerceOrderStatus.PAID
        order.payment_method = payment_method
        order.transaction_id = transaction_id
        order.paid_at = datetime.now()

        items = await cls.get_order_items(db, order.id)
        vip_items = [item for item in items if item.product_type == "vip"]
        if vip_items:
            vip_user = await db.scalar(select(UserAccount).where(UserAccount.id == order.user_id).with_for_update())
            if vip_user:
                now = datetime.now()
                for item in vip_items:
                    plan = await get_vip_plan(db, abs(int(item.product_id)), include_disabled=True)
                    if not plan:
                        continue
                    current_end = vip_user.vip_expires_at if vip_user.vip_enabled and vip_user.vip_expires_at and vip_user.vip_expires_at > now else now
                    vip_user.vip_enabled = True
                    vip_user.vip_type = plan["vip_type"]
                    vip_user.vip_started_at = vip_user.vip_started_at or now
                    vip_user.vip_expires_at = current_end + timedelta(days=plan["duration_days"])
                    vip_user.vip_remark = "App VIP 订单自动开通"
                    item.status = CommerceOrderStatus.PAID
        study_items = [item for item in items if item.product_type != "vip"]
        products = {
            item.id: item
            for item in list(await db.scalars(select(StudyProduct).where(StudyProduct.id.in_({row.product_id for row in study_items}))))
        }
        for item in study_items:
            product = products.get(item.product_id)
            if not product:
                continue
            product.sales += item.quantity
            for _ in range(item.quantity):
                db.add(
                    UserEntitlement(
                        user_id=order.user_id,
                        product_id=item.product_id,
                        order_id=order.id,
                        entitlement_type=item.product_type,
                        expires_at=cls._entitlement_expiry(product),
                    )
                )
            item.status = CommerceOrderStatus.PAID

        if study_items and not await db.scalar(select(LearningProfile).where(LearningProfile.user_id == order.user_id)):
            db.add(LearningProfile(user_id=order.user_id))

        rule = await db.get(PointRule, 1) if (study_items or vip_items) else None
        is_vip_order = bool(vip_items) and not study_items
        reward = int(rule.vip_purchase_score or 0) if is_vip_order and rule and rule.enabled and rule.vip_purchase_enabled else int(rule.purchase_score or 0) if study_items and rule and rule.enabled and rule.purchase_enabled else 0
        if reward > 0:
            point_user = await PointService.lock_user(db, order.user_id)
            valid_days = max(1, int(rule.valid_days or 365))
            await PointService.credit_locked(
                db,
                point_user,
                reward,
                biz_type="vip_purchase" if is_vip_order else "study_purchase",
                biz_id=order.id,
                biz_no=order.order_no,
                idempotency_key=f"vip-purchase:{order.id}" if is_vip_order else f"standard-purchase:{order.id}",
                remark="购买 VIP 会员奖励" if is_vip_order else "购买学习产品奖励",
                expires_at=datetime.now() + timedelta(days=valid_days),
            )

        await db.commit()
        await db.refresh(order)
        logger.info("order_paid", extra={"order_no": order.order_no, "user_id": order.user_id})
        return order

    @classmethod
    async def pay_with_balance(cls, db: AsyncSession, user: UserAccount, order: CommerceOrder) -> CommerceOrder:
        """Pay a pending order with the user's wallet balance.

        Args:
            db: SQLAlchemy async session.
            user: Current user.
            order: Pending commerce order.

        Returns:
            Paid order with entitlements granted.

        Raises:
            BusinessError: If the order is not payable or the wallet balance is insufficient.
        """
        locked_order = await db.scalar(select(CommerceOrder).where(CommerceOrder.id == order.id).with_for_update())
        if not locked_order or locked_order.user_id != user.id:
            raise BusinessError("订单不存在", status_code=404, code="ORDER_NOT_FOUND")
        # Idempotent replay of an already-paid order is the normal case for
        # retries; inconsistent rows (paid + canceled, or non-pending statuses)
        # are rejected.
        if locked_order.payment_status == CommercePaymentStatus.PAID and locked_order.status == CommerceOrderStatus.PAID:
            return locked_order
        if locked_order.status != CommerceOrderStatus.PENDING:
            raise BusinessError("订单当前状态不可支付", status_code=400, code="ORDER_NOT_PAYABLE")
        if locked_order.payment_status == CommercePaymentStatus.PAID:
            raise BusinessError("订单当前状态不可支付", status_code=400, code="ORDER_NOT_PAYABLE")

        locked_user = await db.scalar(select(UserAccount).where(UserAccount.id == user.id).with_for_update())
        if not locked_user:
            raise BusinessError("用户不存在", status_code=404, code="USER_NOT_FOUND")
        amount = Decimal(locked_order.payable_amount or 0).quantize(Decimal("0.01"))
        if amount < 0:
            raise BusinessError("订单金额异常", status_code=400, code="INVALID_ORDER_AMOUNT")
        before = Decimal(locked_user.balance or 0).quantize(Decimal("0.01"))
        if before < amount:
            raise BusinessError("余额不足，请先充值后再购买", status_code=400, code="INSUFFICIENT_BALANCE")
        after = before - amount
        locked_user.balance = after
        transaction_no = snowflake.next_order_no("WT")
        db.add(
            WalletTransaction(
                user_id=locked_user.id,
                user_no=locked_user.user_no,
                transaction_no=transaction_no,
                direction="expense",
                amount=amount,
                balance_before=before,
                balance_after=after,
                biz_type="purchase",
                biz_id=locked_order.id,
                biz_no=locked_order.order_no,
                remark="购买学习产品",
            )
        )
        logger.info("wallet_balance_deducted", extra={"order_no": locked_order.order_no, "user_id": user.id, "amount": str(amount)})
        return await cls.mark_order_paid(db, locked_order, payment_method="balance", transaction_id=transaction_no)

    @classmethod
    async def cancel_unpaid_order_by_no(cls, order_no: str) -> dict[str, Any]:
        """Cancel an unpaid order and restore stock by order number.

        Args:
            order_no: Order number.

        Returns:
            Cancellation result summary.
        """
        async with SessionLocal() as db:
            order = await cls.get_order_by_no_for_update(db, order_no)
            if not order:
                logger.warning("cancel_order_not_found", extra={"order_no": order_no})
                return {"ok": False, "reason": "not_found", "order_no": order_no}
            if order.payment_method == "wechat":
                from app.services.wechat_pay_service import (
                    close_transaction,
                    is_wechat_pay_configured,
                    query_transaction,
                    validate_successful_transaction,
                )

                if is_wechat_pay_configured():
                    try:
                        transaction = await query_transaction(order.order_no)
                    except BusinessError as exc:
                        if exc.code != "WECHAT_PAY_ORDER_NOT_EXIST":
                            logger.exception("wechat_order_query_before_cancel_failed", extra={"order_no": order_no})
                            return {"ok": False, "reason": "wechat_query_failed", "order_no": order_no}
                    else:
                        trade_state = transaction.get("trade_state")
                        if trade_state == "SUCCESS":
                            transaction_id = validate_successful_transaction(order, transaction)
                            await cls.mark_order_paid(
                                db,
                                order,
                                payment_method="wechat",
                                transaction_id=transaction_id,
                            )
                            return {"ok": True, "changed": True, "reason": "wechat_paid", "order_no": order_no}
                        if trade_state == "USERPAYING":
                            return {"ok": False, "reason": "wechat_user_paying", "order_no": order_no}
                        if trade_state == "NOTPAY":
                            try:
                                await close_transaction(order.order_no)
                            except BusinessError:
                                logger.exception("wechat_order_close_failed", extra={"order_no": order_no})
                                return {"ok": False, "reason": "wechat_close_failed", "order_no": order_no}
            changed = await cls.cancel_unpaid_order(db, order, reason="支付超时，系统自动取消")
            return {"ok": True, "changed": changed, "order_no": order_no}

    @classmethod
    async def finalize_refund(
        cls,
        db: AsyncSession,
        order: CommerceOrder,
        *,
        refund_no: str,
        refund_id: str = "",
        wallet_refund: bool = False,
    ) -> CommerceOrder:
        """Apply local side effects only after a refund is confirmed successful."""
        locked_order = await db.scalar(
            select(CommerceOrder).where(CommerceOrder.id == order.id).with_for_update()
        )
        if not locked_order:
            raise BusinessError("订单不存在", status_code=404, code="ORDER_NOT_FOUND")
        if locked_order.payment_status == CommercePaymentStatus.REFUNDED:
            return locked_order
        if locked_order.payment_status != CommercePaymentStatus.PAID or locked_order.status != CommerceOrderStatus.PAID:
            raise BusinessError("仅已支付订单可以退款", status_code=400, code="ORDER_NOT_REFUNDABLE")

        user = await PointService.lock_user(db, locked_order.user_id)
        if wallet_refund:
            existing_wallet_refund = await db.scalar(
                select(WalletTransaction).where(
                    WalletTransaction.biz_type == "refund",
                    WalletTransaction.biz_no == locked_order.order_no,
                )
            )
            if not existing_wallet_refund:
                amount = Decimal(locked_order.payable_amount or 0).quantize(Decimal("0.01"))
                before = Decimal(user.balance or 0).quantize(Decimal("0.01"))
                user.balance = before + amount
                db.add(
                    WalletTransaction(
                        user_id=user.id,
                        user_no=user.user_no,
                        transaction_no=snowflake.next_order_no("WT"),
                        direction="income",
                        amount=amount,
                        balance_before=before,
                        balance_after=before + amount,
                        biz_type="refund",
                        biz_id=locked_order.id,
                        biz_no=locked_order.order_no,
                        remark="平台退款",
                    )
                )

        items = await cls.get_order_items(db, locked_order.id)
        is_vip_order = bool(items) and all(item.product_type == "vip" for item in items)
        await PointService.revoke_credit_locked(
            db,
            user,
            credit_idempotency_key=f"vip-purchase:{locked_order.id}" if is_vip_order else f"standard-purchase:{locked_order.id}",
            reversal_idempotency_key=f"vip-purchase-refund:{locked_order.id}" if is_vip_order else f"standard-purchase-refund:{locked_order.id}",
            biz_type="vip_purchase_refund" if is_vip_order else "study_purchase_refund",
            biz_id=locked_order.id,
            biz_no=locked_order.order_no,
            remark="VIP 订单退款撤销奖励积分" if is_vip_order else "订单退款撤销购买奖励积分",
        )

        entitlements = list(
            await db.scalars(
                select(UserEntitlement)
                .where(UserEntitlement.order_id == locked_order.id)
                .with_for_update()
            )
        )
        for entitlement in entitlements:
            entitlement.status = "expired"

        product_ids = {item.product_id for item in items}
        products = {
            product.id: product
            for product in list(
                await db.scalars(
                    select(StudyProduct)
                    .where(StudyProduct.id.in_(product_ids))
                    .with_for_update()
                )
            )
        }
        for item in items:
            product = products.get(item.product_id)
            if product:
                product.sales = max(0, int(product.sales or 0) - item.quantity)
            if item.stock_deducted:
                restored_db = bool(product and product.stock >= 0)
                if restored_db:
                    product.stock += item.quantity
                    try:
                        await redis.incr_stock(item.product_id, item.quantity)
                    except RedisUnavailableError:
                        logger.exception("refund_redis_restore_failed", extra={"product_id": item.product_id})
                item.stock_deducted = False
            item.status = CommerceOrderStatus.CANCELED

        locked_order.refund_no = refund_no
        locked_order.refund_id = refund_id
        locked_order.refund_status = "success"
        locked_order.refunded_at = datetime.now()
        locked_order.payment_status = CommercePaymentStatus.REFUNDED
        locked_order.status = CommerceOrderStatus.CANCELED
        locked_order.cancel_reason = "平台退款"
        locked_order.canceled_at = locked_order.refunded_at
        await db.commit()
        await db.refresh(locked_order)
        return locked_order

    @classmethod
    async def cancel_unpaid_order(cls, db: AsyncSession, order: CommerceOrder, reason: str) -> bool:
        """Cancel an unpaid pending order and restore MySQL/Redis stock.

        Args:
            db: SQLAlchemy async session.
            order: Order to cancel.
            reason: Cancellation reason.

        Returns:
            Whether the order was changed.
        """
        if order.payment_status == CommercePaymentStatus.PAID or order.status != CommerceOrderStatus.PENDING:
            logger.info("skip_cancel_order_not_pending", extra={"order_no": order.order_no, "status": order.status})
            return False

        items = await cls.get_order_items(db, order.id)
        product_ids = {item.product_id for item in items if item.stock_deducted}
        products = {
            item.id: item
            for item in list(await db.scalars(select(StudyProduct).where(StudyProduct.id.in_(product_ids)).with_for_update()))
        }
        for item in items:
            if not item.stock_deducted:
                item.status = CommerceOrderStatus.CANCELED
                continue
            product = products.get(item.product_id)
            restored_db = bool(product and product.stock >= 0)
            if restored_db:
                product.stock += item.quantity
            if restored_db:
                # Keep Redis aligned with MySQL: only restore Redis when the
                # database restore actually happened, otherwise the two stores
                # drift permanently.
                try:
                    await redis.incr_stock(item.product_id, item.quantity)
                except RedisUnavailableError:
                    logger.exception("redis_restore_stock_failed", extra={"order_no": order.order_no, "product_id": item.product_id})
            item.stock_deducted = False
            item.status = CommerceOrderStatus.CANCELED

        order.status = CommerceOrderStatus.CANCELED
        order.payment_status = CommercePaymentStatus.CANCELED
        order.cancel_reason = reason
        order.canceled_at = datetime.now()
        await db.commit()
        logger.info("order_canceled_stock_restored", extra={"order_no": order.order_no})
        return True

    @staticmethod
    async def get_order_items(db: AsyncSession, order_id: int) -> list[CommerceOrderItem]:
        """Return all items of an order."""
        return list(await db.scalars(select(CommerceOrderItem).where(CommerceOrderItem.order_id == order_id).order_by(CommerceOrderItem.id)))

    @staticmethod
    async def get_order_items_map(db: AsyncSession, order_ids: list[int]) -> dict[int, list[CommerceOrderItem]]:
        """Batch-load items for many orders to avoid N+1 queries.

        Returns a mapping of order_id -> items sorted by item id.
        """
        if not order_ids:
            return {}
        rows = list(
            await db.scalars(
                select(CommerceOrderItem)
                .where(CommerceOrderItem.order_id.in_(order_ids))
                .order_by(CommerceOrderItem.id)
            )
        )
        result: dict[int, list[CommerceOrderItem]] = {}
        for item in rows:
            result.setdefault(item.order_id, []).append(item)
        return result

    @staticmethod
    async def get_order_by_no(db: AsyncSession, order_no: str) -> CommerceOrder | None:
        """Return an order by order number."""
        return await db.scalar(select(CommerceOrder).where(CommerceOrder.order_no == order_no))

    @staticmethod
    async def get_order_by_no_for_update(db: AsyncSession, order_no: str) -> CommerceOrder | None:
        """Return an order by order number and lock it for status changes."""
        return await db.scalar(select(CommerceOrder).where(CommerceOrder.order_no == order_no).with_for_update())

    @staticmethod
    async def get_order_by_idempotency_key(db: AsyncSession, user_id: int, idempotency_key: str) -> CommerceOrder | None:
        """Return a user's existing order created by an idempotency key."""
        return await db.scalar(
            select(CommerceOrder)
            .where(
                CommerceOrder.user_id == user_id,
                CommerceOrder.idempotency_key == idempotency_key,
            )
            .order_by(CommerceOrder.id.desc())
        )

    @classmethod
    async def _persist_order(
        cls,
        db: AsyncSession,
        user: UserAccount,
        payload: OrderCreateRequest,
        products: dict[int, StudyProduct],
        quantity_map: dict[int, int],
    ) -> CommerceOrder:
        """Persist order header, item rows and MySQL stock deduction."""
        order_no = snowflake.next_order_no("XO")
        total_amount = sum((Decimal(products[pid].price) * quantity for pid, quantity in quantity_map.items()), Decimal("0.00"))
        if total_amount < 0:
            raise BusinessError("订单金额异常", status_code=400, code="INVALID_ORDER_AMOUNT")
        school_ids = {products[pid].school_id for pid in quantity_map}
        order = CommerceOrder(
            order_no=order_no,
            user_id=user.id,
            school_id=next(iter(school_ids)) if len(school_ids) == 1 else 0,
            total_amount=total_amount,
            payable_amount=total_amount,
            payment_method=payload.payment_method,
            payment_status=CommercePaymentStatus.PENDING,
            status=CommerceOrderStatus.PENDING,
            idempotency_key=payload.idempotency_key,
        )
        db.add(order)
        await db.flush()

        for product_id, quantity in quantity_map.items():
            product = products[product_id]
            stock_deducted = product.stock >= 0
            if product.stock >= 0:
                product.stock -= quantity
            db.add(
                CommerceOrderItem(
                    order_id=order.id,
                    order_no=order_no,
                    product_id=product.id,
                    product_name=product.name,
                    product_type=product.product_type,
                    school_id=product.school_id,
                    unit_price=product.price,
                    quantity=quantity,
                    total_amount=Decimal(product.price) * quantity,
                    stock_deducted=stock_deducted,
                    status=CommerceOrderStatus.PENDING,
                )
            )

        await db.commit()
        await db.refresh(order)
        return order

    @staticmethod
    def _merge_items(payload: OrderCreateRequest) -> dict[int, int]:
        """Merge duplicate product rows into quantity per product."""
        quantity_map: dict[int, int] = defaultdict(int)
        for item in payload.items:
            quantity_map[item.product_id] += item.quantity
        return dict(quantity_map)

    @staticmethod
    async def _load_products(db: AsyncSession, quantity_map: dict[int, int]) -> dict[int, StudyProduct]:
        """Load products with row locks to keep MySQL stock aligned."""
        products = list(
            await db.scalars(
                select(StudyProduct)
                .where(StudyProduct.id.in_(quantity_map.keys()))
                .with_for_update()
            )
        )
        return {item.id: item for item in products}

    @staticmethod
    def _validate_products(products: dict[int, StudyProduct], quantity_map: dict[int, int]) -> None:
        """Validate product status and finite database stock."""
        for product_id, quantity in quantity_map.items():
            product = products.get(product_id)
            if not product or not product.status or product.review_status != ReviewStatus.APPROVED:
                raise BusinessError(f"商品 {product_id} 不存在或已下架", status_code=404, code="PRODUCT_NOT_AVAILABLE")
            if product.stock >= 0 and product.stock < quantity:
                raise InsufficientStockError(f"商品「{product.name}」库存不足")

    @staticmethod
    async def _pre_deduct_stock(products: dict[int, StudyProduct], quantity_map: dict[int, int], deducted: list[tuple[int, int]]) -> None:
        """Pre-deduct finite stock using Redis DECRBY.

        If any item becomes negative, this method rolls back all Redis
        deductions, including the failed item, before raising.
        """
        for product_id, quantity in quantity_map.items():
            product = products[product_id]
            if product.stock < 0:
                continue
            try:
                await redis.set_stock_if_absent(product_id, product.stock)
                remaining = await redis.decr_stock(product_id, quantity)
                deducted.append((product_id, quantity))
            except RedisUnavailableError as exc:
                raise RedisRequiredError() from exc
            if remaining < 0:
                await OrderService._rollback_redis_stock(deducted)
                raise InsufficientStockError(f"商品「{product.name}」库存不足")

    @staticmethod
    async def _rollback_redis_stock(deducted: list[tuple[int, int]]) -> None:
        """Rollback all Redis stock deductions."""
        for product_id, quantity in reversed(deducted):
            try:
                await redis.incr_stock(product_id, quantity)
            except RedisUnavailableError:
                logger.exception("redis_stock_rollback_failed", extra={"product_id": product_id, "quantity": quantity})
        deducted.clear()

    @staticmethod
    def _entitlement_expiry(product: StudyProduct) -> datetime | None:
        """Calculate entitlement expiry by product billing cycle."""
        if product.billing_cycle == "month":
            return datetime.now() + timedelta(days=30)
        if product.billing_cycle == "year":
            return datetime.now() + timedelta(days=365)
        return None
