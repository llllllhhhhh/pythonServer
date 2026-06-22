import json
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings


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


async def close_cache() -> None:
    if redis_client is not None:
        await redis_client.aclose()
