from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    return hash_password(password, salt) == stored_hash


def generate_token() -> str:
    return secrets.token_urlsafe(36)


def generate_user_no(prefix: str = "U") -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(900) + 100}"


def session_expire_at(days: int = 30) -> datetime:
    return datetime.now() + timedelta(days=days)
