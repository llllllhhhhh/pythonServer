import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from redis import Redis as SyncRedis
from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)


def enqueue_order_payment_timeout(order_no: str) -> None:
    """Enqueue a delayed job to cancel unpaid orders after the payment window.

    The worker can be started with:
        rq worker xuetuxing-orders --with-scheduler

    Args:
        order_no: Commerce order number.
    """
    if not settings.redis_enabled:
        logger.warning("skip_order_timeout_job_redis_disabled", extra={"order_no": order_no})
        return
    try:
        from rq import Queue

        connection = SyncRedis.from_url(settings.redis_url)
        queue = Queue("xuetuxing-orders", connection=connection)
        queue.enqueue_in(
            timedelta(minutes=settings.order_payment_timeout_minutes),
            cancel_unpaid_order_task,
            order_no,
            job_timeout=120,
        )
        logger.info("order_timeout_job_enqueued", extra={"order_no": order_no})
    except Exception:  # pragma: no cover - deployment/runtime safety
        logger.exception("order_timeout_job_enqueue_failed", extra={"order_no": order_no})
        _mark_timeout_job_unscheduled(order_no)


def _mark_timeout_job_unscheduled(order_no: str) -> None:
    """Best-effort flag that the delayed job was never scheduled.

    The sweep task (sweep_unscheduled_timeouts) will pick up orders with
    ``timeout_job_scheduled = false`` and cancel them after the payment
    window, so an enqueue failure no longer leaks stock forever.
    """
    try:
        from sqlalchemy import update

        from app.core.database import SessionLocal
        from app.models import CommerceOrder

        async def _mark() -> None:
            async with SessionLocal() as db:
                await db.execute(
                    update(CommerceOrder)
                    .where(CommerceOrder.order_no == order_no)
                    .values(timeout_job_scheduled=False)
                )
                await db.commit()

        asyncio.run(_mark())
    except Exception:  # pragma: no cover - best effort
        logger.exception("order_timeout_mark_unscheduled_failed", extra={"order_no": order_no})


def cancel_unpaid_order_task(order_no: str) -> dict[str, Any]:
    """RQ task entrypoint that cancels an unpaid order and restores stock.

    Args:
        order_no: Commerce order number.

    Returns:
        Task result summary.
    """
    from app.services.order_service import OrderService

    return asyncio.run(OrderService.cancel_unpaid_order_by_no(order_no))


def sweep_unscheduled_timeouts() -> dict[str, Any]:
    """RQ scheduler task: cancel pending orders whose delayed job was never scheduled.

    Runs as a fallback for orders where ``enqueue_order_payment_timeout``
    failed (Redis/worker unavailable at creation time). Cancels orders older
    than the payment window that are still pending and unscheduled.
    """
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import CommerceOrder
    from app.services.order_service import OrderService

    async def _sweep() -> dict[str, Any]:
        async with SessionLocal() as db:
            cutoff = datetime.now() - timedelta(minutes=settings.order_payment_timeout_minutes)
            rows = list(
                await db.scalars(
                    select(CommerceOrder).where(
                        CommerceOrder.timeout_job_scheduled.is_(False),
                        CommerceOrder.status == "pending",
                        CommerceOrder.created_at < cutoff,
                    )
                )
            )
        canceled = []
        for order in rows:
            result = await OrderService.cancel_unpaid_order_by_no(order.order_no)
            if result.get("ok") and result.get("changed"):
                canceled.append(order.order_no)
        return {"scanned": len(rows), "canceled": canceled}

    try:
        return asyncio.run(_sweep())
    except Exception:  # pragma: no cover - deployment/runtime safety
        logger.exception("order_timeout_sweep_failed")
        return {"scanned": 0, "canceled": []}
