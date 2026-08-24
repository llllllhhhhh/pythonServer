from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def _legacy_hash_password(password: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("$argon2"):
        try:
            return bool(password_hasher.verify(stored_hash, password))
        except (VerifyMismatchError, VerificationError):
            return False
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_legacy_hash_password(password, salt), stored_hash)


def verify_and_rehash_password(password: str, stored_hash: str) -> tuple[bool, str | None]:
    if not verify_password(password, stored_hash):
        return False, None
    if not stored_hash.startswith("$argon2") or password_hasher.check_needs_rehash(stored_hash):
        return True, hash_password(password)
    return True, None


def generate_token() -> str:
    return secrets.token_urlsafe(36)


def generate_user_no(prefix: str = "U") -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(9000) + 1000}"


def session_expire_at(days: int = 30) -> datetime:
    return datetime.now() + timedelta(days=days)
