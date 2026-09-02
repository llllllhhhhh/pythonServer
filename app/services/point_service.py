from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.snowflake import SnowflakeGenerator
from app.core.config import settings
from app.models import PointLedger, PointLot, PointLotAllocation, UserAccount


snowflake = SnowflakeGenerator(settings.snowflake_worker_id, settings.snowflake_datacenter_id)


class PointService:
    """Apply every point balance change together with its immutable ledger rows."""

    @staticmethod
    async def lock_user(db: AsyncSession, user_id: int) -> UserAccount:
        user = await db.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
        if not user:
            raise BusinessError("用户不存在", status_code=404, code="USER_NOT_FOUND")
        return user

    @staticmethod
    async def find_ledger(db: AsyncSession, user_id: int, idempotency_key: str) -> PointLedger | None:
        return await db.scalar(
            select(PointLedger).where(
                PointLedger.user_id == user_id,
                PointLedger.idempotency_key == idempotency_key,
            )
        )

    @classmethod
    async def revoke_credit_locked(
        cls,
        db: AsyncSession,
        user: UserAccount,
        *,
        credit_idempotency_key: str,
        reversal_idempotency_key: str,
        biz_type: str,
        biz_id: int = 0,
        biz_no: str = "",
        remark: str = "",
    ) -> PointLedger | None:
        """Remove the unspent remainder of an earlier point credit.

        A refund must never fail merely because the user has already consumed
        reward points. Only the source lot's remaining amount is revoked.
        """
        existing = await cls.find_ledger(db, user.id, reversal_idempotency_key)
        if existing:
            return existing
        original = await cls.find_ledger(db, user.id, credit_idempotency_key)
        if not original or original.direction != "income":
            return None
        lot = await db.scalar(
            select(PointLot)
            .where(PointLot.source_ledger_id == original.id)
            .with_for_update()
        )
        if not lot or lot.status == "revoked":
            return None
        amount = min(int(lot.remaining_amount or 0), int(user.points or 0))
        lot.remaining_amount = max(0, int(lot.remaining_amount or 0) - amount)
        lot.status = "revoked" if lot.remaining_amount == 0 else lot.status
        if amount <= 0:
            lot.status = "revoked"
            return None
        before = int(user.points or 0)
        ledger = cls._ledger(
            user,
            direction="expense",
            amount=amount,
            balance_before=before,
            balance_after=before - amount,
            biz_type=biz_type,
            biz_id=biz_id,
            biz_no=biz_no,
            idempotency_key=reversal_idempotency_key,
            remark=remark,
        )
        user.points = before - amount
        db.add(ledger)
        return ledger

    @classmethod
    async def credit_locked(
        cls,
        db: AsyncSession,
        user: UserAccount,
        amount: int,
        *,
        biz_type: str,
        biz_id: int = 0,
        biz_no: str = "",
        idempotency_key: str,
        remark: str = "",
        expires_at: datetime | None = None,
    ) -> PointLedger:
        amount = int(amount)
        if amount <= 0:
            raise BusinessError("积分变动数量必须大于 0", status_code=400, code="INVALID_POINT_AMOUNT")
        existing = await cls.find_ledger(db, user.id, idempotency_key)
        if existing:
            return existing

        before = int(user.points or 0)
        after = before + amount
        ledger = cls._ledger(
            user,
            direction="income",
            amount=amount,
            balance_before=before,
            balance_after=after,
            biz_type=biz_type,
            biz_id=biz_id,
            biz_no=biz_no,
            idempotency_key=idempotency_key,
            remark=remark,
        )
        user.points = after
        db.add(ledger)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await cls.find_ledger(db, user.id, idempotency_key)
            if existing:
                return existing
            raise
        db.add(
            PointLot(
                user_id=user.id,
                source_ledger_id=ledger.id,
                original_amount=amount,
                remaining_amount=amount,
                expires_at=expires_at,
                status="active",
            )
        )
        return ledger

    @classmethod
    async def debit_locked(
        cls,
        db: AsyncSession,
        user: UserAccount,
        amount: int,
        *,
        biz_type: str,
        biz_id: int = 0,
        biz_no: str = "",
        idempotency_key: str,
        remark: str = "",
    ) -> PointLedger:
        amount = int(amount)
        if amount <= 0:
            raise BusinessError("积分变动数量必须大于 0", status_code=400, code="INVALID_POINT_AMOUNT")
        existing = await cls.find_ledger(db, user.id, idempotency_key)
        if existing:
            return existing

        await cls.expire_due_locked(db, user)
        before = int(user.points or 0)
        if before < amount:
            raise BusinessError("积分不足", status_code=400, code="INSUFFICIENT_POINTS")
        await cls._ensure_lot_coverage(db, user)

        now = datetime.now()
        lots = list(
            await db.scalars(
                select(PointLot)
                .where(
                    PointLot.user_id == user.id,
                    PointLot.status == "active",
                    PointLot.remaining_amount > 0,
                    or_(PointLot.expires_at.is_(None), PointLot.expires_at > now),
                )
                .order_by(
                    case((PointLot.expires_at.is_(None), 1), else_=0),
                    PointLot.expires_at,
                    PointLot.id,
                )
                .with_for_update()
            )
        )
        if sum(int(lot.remaining_amount or 0) for lot in lots) < amount:
            raise BusinessError("积分批次余额异常，请联系平台处理", status_code=409, code="POINT_LEDGER_MISMATCH")

        ledger = cls._ledger(
            user,
            direction="expense",
            amount=amount,
            balance_before=before,
            balance_after=before - amount,
            biz_type=biz_type,
            biz_id=biz_id,
            biz_no=biz_no,
            idempotency_key=idempotency_key,
            remark=remark,
        )
        user.points = before - amount
        db.add(ledger)
        await db.flush()

        remaining = amount
        for lot in lots:
            if remaining <= 0:
                break
            consumed = min(int(lot.remaining_amount or 0), remaining)
            lot.remaining_amount = int(lot.remaining_amount or 0) - consumed
            if lot.remaining_amount == 0:
                lot.status = "consumed"
            db.add(
                PointLotAllocation(
                    expense_ledger_id=ledger.id,
                    lot_id=lot.id,
                    amount=consumed,
                )
            )
            remaining -= consumed
        return ledger

    @classmethod
    async def refund_locked(
        cls,
        db: AsyncSession,
        user: UserAccount,
        *,
        original_biz_type: str,
        biz_no: str,
        refund_biz_type: str,
        idempotency_key: str,
        biz_id: int = 0,
        remark: str = "",
        expired_lot_fallback: datetime | None = None,
    ) -> PointLedger:
        existing = await cls.find_ledger(db, user.id, idempotency_key)
        if existing:
            return existing
        original = await db.scalar(
            select(PointLedger).where(
                PointLedger.user_id == user.id,
                PointLedger.biz_type == original_biz_type,
                PointLedger.biz_no == biz_no,
                PointLedger.direction == "expense",
            )
        )
        if not original:
            raise BusinessError("未找到原积分扣减流水", status_code=409, code="POINT_DEBIT_NOT_FOUND")

        amount = int(original.amount)
        before = int(user.points or 0)
        ledger = cls._ledger(
            user,
            direction="income",
            amount=amount,
            balance_before=before,
            balance_after=before + amount,
            biz_type=refund_biz_type,
            biz_id=biz_id,
            biz_no=biz_no,
            idempotency_key=idempotency_key,
            remark=remark,
        )
        user.points = before + amount
        db.add(ledger)
        await db.flush()

        allocations = list(
            await db.scalars(
                select(PointLotAllocation)
                .where(PointLotAllocation.expense_ledger_id == original.id)
                .order_by(PointLotAllocation.id)
                .with_for_update()
            )
        )
        restored = 0
        now = datetime.now()
        for allocation in allocations:
            restorable = int(allocation.amount or 0) - int(allocation.restored_amount or 0)
            if restorable <= 0:
                continue
            lot = await db.scalar(select(PointLot).where(PointLot.id == allocation.lot_id).with_for_update())
            if not lot:
                continue
            lot.remaining_amount = int(lot.remaining_amount or 0) + restorable
            lot.status = "active"
            if lot.expires_at and lot.expires_at <= now:
                lot.expires_at = expired_lot_fallback
            allocation.restored_amount = int(allocation.restored_amount or 0) + restorable
            restored += restorable

        if restored < amount:
            remainder = amount - restored
            db.add(
                PointLot(
                    user_id=user.id,
                    source_ledger_id=ledger.id,
                    original_amount=remainder,
                    remaining_amount=remainder,
                    expires_at=expired_lot_fallback,
                    status="active",
                )
            )
        return ledger

    @classmethod
    async def expire_due_locked(cls, db: AsyncSession, user: UserAccount) -> PointLedger | None:
        now = datetime.now()
        lots = list(
            await db.scalars(
                select(PointLot)
                .where(
                    PointLot.user_id == user.id,
                    PointLot.status == "active",
                    PointLot.remaining_amount > 0,
                    PointLot.expires_at.is_not(None),
                    PointLot.expires_at <= now,
                )
                .order_by(PointLot.id)
                .with_for_update()
            )
        )
        if not lots:
            return None
        amount = min(sum(int(lot.remaining_amount or 0) for lot in lots), int(user.points or 0))
        if amount <= 0:
            for lot in lots:
                lot.remaining_amount = 0
                lot.status = "expired"
            return None

        before = int(user.points or 0)
        max_lot_id = max(lot.id for lot in lots)
        ledger = cls._ledger(
            user,
            direction="expense",
            amount=amount,
            balance_before=before,
            balance_after=before - amount,
            biz_type="points_expired",
            biz_id=max_lot_id,
            biz_no=str(max_lot_id),
            idempotency_key=f"points-expired:{user.id}:{max_lot_id}",
            remark="积分到期扣减",
        )
        user.points = before - amount
        db.add(ledger)
        await db.flush()
        remaining = amount
        for lot in lots:
            expired = int(lot.remaining_amount or 0)
            lot.remaining_amount = 0
            lot.status = "expired"
            if remaining > 0:
                allocated = min(expired, remaining)
                db.add(PointLotAllocation(expense_ledger_id=ledger.id, lot_id=lot.id, amount=allocated))
                remaining -= allocated
        return ledger

    @classmethod
    async def _ensure_lot_coverage(cls, db: AsyncSession, user: UserAccount) -> None:
        now = datetime.now()
        covered = int(
            await db.scalar(
                select(func.coalesce(func.sum(PointLot.remaining_amount), 0)).where(
                    PointLot.user_id == user.id,
                    PointLot.status == "active",
                    PointLot.remaining_amount > 0,
                    or_(PointLot.expires_at.is_(None), PointLot.expires_at > now),
                )
            )
            or 0
        )
        balance = int(user.points or 0)
        if covered >= balance:
            return
        missing = balance - covered
        ledger = cls._ledger(
            user,
            direction="income",
            amount=missing,
            balance_before=covered,
            balance_after=balance,
            biz_type="legacy_opening",
            biz_id=user.id,
            biz_no=user.user_no,
            idempotency_key=f"legacy-opening:{user.id}",
            remark="历史积分期初余额",
        )
        db.add(ledger)
        await db.flush()
        db.add(
            PointLot(
                user_id=user.id,
                source_ledger_id=ledger.id,
                original_amount=missing,
                remaining_amount=missing,
                expires_at=None,
                status="active",
            )
        )
        await db.flush()

    @staticmethod
    def _ledger(
        user: UserAccount,
        *,
        direction: str,
        amount: int,
        balance_before: int,
        balance_after: int,
        biz_type: str,
        biz_id: int,
        biz_no: str,
        idempotency_key: str,
        remark: str,
    ) -> PointLedger:
        return PointLedger(
            transaction_no=snowflake.next_order_no("PL"),
            user_id=user.id,
            user_no=user.user_no,
            direction=direction,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            biz_type=biz_type,
            biz_id=biz_id,
            biz_no=biz_no,
            idempotency_key=idempotency_key[:160],
            remark=remark[:255],
        )
