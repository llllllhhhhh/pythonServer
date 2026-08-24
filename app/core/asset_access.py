from __future__ import annotations

import hashlib
import hmac
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import settings


PUBLIC_ASSET_SOURCES = frozenset(
    {
        "common",
        "decoration",
        "route",
        "study_product",
        "article",
        "travel_review",
    }
)
ASSET_PATH_PATTERN = re.compile(
    rf"{re.escape(settings.api_prefix)}/public/assets/(?P<asset_id>\d+)/(?P<variant>file|thumb)"
)


def is_public_asset_source(source: str | None) -> bool:
    return str(source or "").strip().lower() in PUBLIC_ASSET_SOURCES


def create_asset_access_token(
    asset_id: int,
    variant: str = "file",
    expires_at: int | None = None,
) -> str:
    expires = expires_at or int(time.time()) + settings.asset_url_ttl_seconds
    payload = f"{int(asset_id)}:{expires}".encode("utf-8")
    signature = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"{expires}.{signature}"


def verify_asset_access_token(
    asset_id: int,
    variant: str,
    token: str | None,
    now: int | None = None,
) -> bool:
    if not token or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    current_time = int(time.time()) if now is None else now
    if expires < current_time:
        return False
    expected = create_asset_access_token(asset_id, variant, expires)
    return hmac.compare_digest(expected, token)


def asset_api_url(asset_id: int, variant: str = "file", signed: bool = False) -> str:
    path = f"{settings.api_prefix}/public/assets/{int(asset_id)}/{variant}"
    if not signed:
        return path
    return f"{path}?asset_token={create_asset_access_token(asset_id, variant)}"


def extract_asset_reference(url: str | None) -> tuple[int, str] | None:
    if not url:
        return None
    match = ASSET_PATH_PATTERN.search(urlsplit(str(url)).path)
    if not match:
        return None
    return int(match.group("asset_id")), match.group("variant")


def canonical_asset_url(url: str | None) -> str:
    reference = extract_asset_reference(url)
    return asset_api_url(*reference) if reference else str(url or "")


def sign_asset_url(url: str | None) -> str:
    raw = str(url or "")
    reference = extract_asset_reference(raw)
    if not reference:
        return raw
    asset_id, variant = reference
    parsed = urlsplit(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["asset_token"] = create_asset_access_token(asset_id, variant)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
