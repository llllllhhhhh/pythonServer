import json
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client = (
    Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    if settings.redis_enabled else None
)


class _MemoryPipeline:
    def __init__(self, client: "_MemoryRedis") -> None:
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> "_MemoryPipeline":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._ops.clear()

    def incr(self, key: str) -> "_MemoryPipeline":
        self._ops.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> "_MemoryPipeline":
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args in self._ops:
            if name == "incr":
                results.append(await self._client.incrby(args[0], 1))
            elif name == "expire":
                results.append(await self._client.expire(args[0], args[1]))
        self._ops.clear()
        return results


class _MemoryRedis:
    """Development-only Redis substitute for local runs without Redis installed."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    def _purge(self, key: str) -> None:
        item = self._store.get(key)
        if item and item[1] is not None and item[1] <= time.time():
            self._store.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        self._purge(key)
        item = self._store.get(key)
        return item[0] if item else None

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        self._purge(key)
        if nx and key in self._store:
            return False
        expires_at = time.time() + ex if ex else None
        self._store[key] = (str(value), expires_at)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._store:
                self._store.pop(key, None)
                count += 1
        return count

    async def ttl(self, key: str) -> int:
        self._purge(key)
        item = self._store.get(key)
        if not item:
            return -2
        if item[1] is None:
            return -1
        return max(0, int(item[1] - time.time()))

    async def expire(self, key: str, seconds: int) -> bool:
        self._purge(key)
        if key not in self._store:
            return False
        value, _expires_at = self._store[key]
        self._store[key] = (value, time.time() + seconds)
        return True

    async def incrby(self, key: str, amount: int) -> int:
        current = int(await self.get(key) or 0) + amount
        await self.set(key, current)
        return current

    async def decrby(self, key: str, amount: int) -> int:
        return await self.incrby(key, -amount)

    def pipeline(self, transaction: bool = True) -> _MemoryPipeline:
        return _MemoryPipeline(self)

    async def aclose(self) -> None:
        self._store.clear()


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

    async def sync_stock(self, product_id: int, stock: int) -> None:
        """Re-sync an existing Redis stock key to the database value.

        Only updates keys that already exist (finite-stock products that have
        been ordered at least once); unlimited-stock products never create a
        key, so admin/merchant edits do not resurrect stale entries.
        """
        await self.raw.set(f"stock:study_product:{product_id}", int(stock), xx=True)

    async def get_stock(self, product_id: int) -> int | None:
        """Return the current Redis stock value, or None when no key exists."""
        value = await self.raw.get(f"stock:study_product:{product_id}")
        return int(value) if value is not None else None

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


async def ensure_cache_available() -> None:
    """Fail fast when Redis is enabled but unreachable."""
    global redis_client
    if redis_client is None:
        raise RedisUnavailableError("Redis is not enabled")
    try:
        await redis_client.ping()
    except Exception as exc:  # pragma: no cover - depends on external Redis.
        if settings.environment.lower() in {"development", "dev", "local"}:
            logger.warning(
                "redis_unreachable_using_memory_cache_for_development",
                extra={"redis_url": settings.redis_url},
            )
            redis_client = _MemoryRedis()
            redis._client = redis_client
            return
        logger.exception("redis_startup_ping_failed")
        raise RedisUnavailableError("Redis is unreachable") from exc


async def close_cache() -> None:
    if redis_client is not None:
        await redis_client.aclose()
