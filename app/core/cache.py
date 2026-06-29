import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client = (
    Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    if settings.redis_enabled else None
)


async def cache_get_json(key: str) -> Any | None:
    if redis_client is None:
        return None
    try:
        value = await redis_client.get(key)
        return json.loads(value) if value else None
    except Exception:
        return None


async def cache_set_json(key: str, value: Any, ttl: int = 300) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:
        pass


async def cache_delete(key: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.delete(key)
    except Exception:
        pass


class RedisUnavailableError(RuntimeError):
    """Raised when Redis is required for a business operation but is disabled."""


class RedisClient:
    """Small Redis wrapper used by business services.

    The existing cache helpers intentionally swallow Redis errors because cache
    misses are acceptable. Order creation is different: idempotency and stock
    pre-deduction depend on Redis atomic operations, so this wrapper raises
    explicit errors when Redis is unavailable or a command fails.
    """

    def __init__(self, client: Redis | None) -> None:
        self._client = client

    @property
    def raw(self) -> Redis:
        """Return the underlying Redis client or raise if Redis is disabled."""
        if self._client is None:
            raise RedisUnavailableError("Redis is not enabled")
        return self._client

    async def setnx(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Set a value only when the key does not exist.

        Args:
            key: Redis key.
            value: String value to store.
            ttl_seconds: Expiration seconds.

        Returns:
            Whether the key was created.
        """
        try:
            return bool(await self.raw.set(key, value, nx=True, ex=ttl_seconds))
        except RedisUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - network/runtime guard
            logger.exception("redis_setnx_failed", extra={"key": key})
            raise RedisUnavailableError(str(exc)) from exc

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a JSON value with TTL."""
        await self.raw.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)

    async def get_json(self, key: str) -> Any | None:
        """Read a JSON value."""
        value = await self.raw.get(key)
        return json.loads(value) if value else None

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys."""
        if keys:
            await self.raw.delete(*keys)

    async def set_stock_if_absent(self, product_id: int, stock: int) -> None:
        """Initialize a product stock key from database stock when missing."""
        await self.raw.set(f"stock:study_product:{product_id}", int(stock), nx=True)

    async def decr_stock(self, product_id: int, quantity: int) -> int:
        """Atomically decrement stock.

        Args:
            product_id: Product id.
            quantity: Quantity to deduct.

        Returns:
            Stock value after decrement.
        """
        return int(await self.raw.decrby(f"stock:study_product:{product_id}", quantity))

    async def incr_stock(self, product_id: int, quantity: int) -> int:
        """Atomically increment stock."""
        return int(await self.raw.incrby(f"stock:study_product:{product_id}", quantity))


redis = RedisClient(redis_client)


async def close_cache() -> None:
    if redis_client is not None:
        await redis_client.aclose()
