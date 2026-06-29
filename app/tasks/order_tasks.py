import asyncio
import logging
from datetime import timedelta
from typing import Any

from redis import Redis as SyncRedis

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


def cancel_unpaid_order_task(order_no: str) -> dict[str, Any]:
    """RQ task entrypoint that cancels an unpaid order and restores stock.

    Args:
        order_no: Commerce order number.

    Returns:
        Task result summary.
    """
    from app.services.order_service import OrderService

    return asyncio.run(OrderService.cancel_unpaid_order_by_no(order_no))
