from __future__ import annotations

import hashlib
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request, status

from app.core.cache import RedisUnavailableError, redis
from app.core.config import settings


def _login_key(scope: str, account: str, client_ip: str) -> str:
    identity = f"{scope}:{account.strip().lower()}:{client_ip}".encode("utf-8")
    return f"security:login:{hashlib.sha256(identity).hexdigest()}"


def _is_trusted_proxy(host: str) -> bool:
    try:
        address = ip_address(host)
    except ValueError:
        return False
    for value in settings.trusted_proxy_list:
        try:
            if address in ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(direct_ip):
        return direct_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    for value in forwarded.split(","):
        candidate = value.strip()
        try:
            return str(ip_address(candidate))
        except ValueError:
            continue
    real_ip = request.headers.get("x-real-ip", "").strip()
    try:
        return str(ip_address(real_ip))
    except ValueError:
        return direct_ip


async def ensure_login_allowed(scope: str, account: str, request: Request) -> str:
    key = _login_key(scope, account, _client_ip(request))
    try:
        attempts = int(await redis.raw.get(key) or 0)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login protection service is unavailable",
        ) from exc
    if attempts >= settings.login_max_attempts:
        ttl = max(1, int(await redis.raw.ttl(key)))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(ttl)},
        )
    return key


async def record_login_failure(key: str) -> None:
    try:
        async with redis.raw.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, settings.login_attempt_window_seconds)
            await pipeline.execute()
    except Exception as exc:
        raise RedisUnavailableError("Login protection service is unavailable") from exc


async def clear_login_failures(key: str) -> None:
    await redis.delete(key)
