from copy import deepcopy
from random import choice, randint
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get_json, cache_set_json
from app.models import SystemSetting


SLIDER_CAPTCHA_SETTING_KEY = "login.slider_captcha"
SLIDER_CAPTCHA_CHALLENGE_PREFIX = "slider:challenge:"
SLIDER_CAPTCHA_TICKET_PREFIX = "slider:ticket:"
SLIDER_CAPTCHA_SCOPES = {"user", "admin", "merchant"}
SLIDER_CAPTCHA_SHAPES = {"rounded", "circle", "square", "puzzle"}

DEFAULT_SLIDER_CAPTCHA_IMAGE: dict[str, Any] = {
    "name": "默认山景",
    "url": "https://images.unsplash.com/photo-1500534314209-a25ddb2bd429?w=900&auto=format&fit=crop",
    "enabled": True,
}

DEFAULT_SLIDER_CAPTCHA_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "title": "拖动滑块完成验证",
    "description": "请按住滑块，将缺口拼合后再登录。",
    "image": DEFAULT_SLIDER_CAPTCHA_IMAGE["url"],
    "images": [DEFAULT_SLIDER_CAPTCHA_IMAGE],
    "shape": "rounded",
    "tolerance": 6,
    "expires_seconds": 120,
}


def _normalize_captcha_images(raw_images: Any, fallback_image: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    source = raw_images if isinstance(raw_images, list) else []
    for index, item in enumerate(source):
        if isinstance(item, str):
            url = item.strip()
            name = f"背景图 {index + 1}"
            enabled = True
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("image") or "").strip()
            name = str(item.get("name") or f"背景图 {index + 1}").strip()
            enabled = item.get("enabled") is not False
        else:
            continue
        if not url:
            continue
        images.append({"name": name[:60], "url": url[:500], "enabled": bool(enabled)})
        if len(images) >= 20:
            break

    if not images and fallback_image:
        images.append({"name": "默认背景", "url": fallback_image[:500], "enabled": True})
    if not any(item["enabled"] for item in images) and images:
        images[0]["enabled"] = True
    return images or [deepcopy(DEFAULT_SLIDER_CAPTCHA_IMAGE)]


def normalize_slider_captcha_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    settings = deepcopy(DEFAULT_SLIDER_CAPTCHA_SETTINGS)
    settings["enabled"] = raw.get("enabled") is not False
    settings["title"] = str(raw.get("title") or settings["title"]).strip()[:80]
    settings["description"] = str(raw.get("description") or settings["description"]).strip()[:160]

    fallback_image = str(raw.get("image") or settings["image"]).strip()
    settings["images"] = _normalize_captcha_images(raw.get("images"), fallback_image or settings["image"])
    enabled_images = [item for item in settings["images"] if item["enabled"]]
    settings["image"] = (enabled_images[0] if enabled_images else settings["images"][0])["url"]

    shape = str(raw.get("shape") or settings["shape"]).strip()
    settings["shape"] = shape if shape in SLIDER_CAPTCHA_SHAPES else DEFAULT_SLIDER_CAPTCHA_SETTINGS["shape"]

    try:
        settings["tolerance"] = max(2, min(20, int(raw.get("tolerance", settings["tolerance"]))))
    except (TypeError, ValueError):
        settings["tolerance"] = DEFAULT_SLIDER_CAPTCHA_SETTINGS["tolerance"]
    try:
        settings["expires_seconds"] = max(30, min(300, int(raw.get("expires_seconds", settings["expires_seconds"]))))
    except (TypeError, ValueError):
        settings["expires_seconds"] = DEFAULT_SLIDER_CAPTCHA_SETTINGS["expires_seconds"]
    return settings


async def get_slider_captcha_settings(db: AsyncSession) -> dict[str, Any]:
    item = await db.get(SystemSetting, SLIDER_CAPTCHA_SETTING_KEY)
    return normalize_slider_captcha_settings(item.value if item else None)


async def save_slider_captcha_settings(db: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_slider_captcha_settings(value)
    item = await db.get(SystemSetting, SLIDER_CAPTCHA_SETTING_KEY)
    if item:
        item.value = normalized
        item.remark = "login slider captcha settings"
    else:
        item = SystemSetting(
            key=SLIDER_CAPTCHA_SETTING_KEY,
            value=normalized,
            remark="login slider captcha settings",
        )
        db.add(item)
    await db.commit()
    return normalized


async def create_slider_challenge(db: AsyncSession) -> dict[str, Any]:
    settings = await get_slider_captcha_settings(db)
    if not settings["enabled"]:
        return {
            "enabled": False,
            "challenge_id": "",
            "title": settings["title"],
            "description": settings["description"],
            "image": settings["image"],
            "shape": settings["shape"],
            "target_x": 0,
            "piece_y": 0,
            "piece_size": 42,
            "expires_seconds": settings["expires_seconds"],
        }

    challenge_id = uuid4().hex
    piece_size = 42
    target_x = randint(118, 236)
    piece_y = randint(38, 112)
    enabled_images = [item for item in settings["images"] if item["enabled"]]
    image = choice(enabled_images or settings["images"])["url"]
    await cache_set_json(
        f"{SLIDER_CAPTCHA_CHALLENGE_PREFIX}{challenge_id}",
        {
            "target_x": target_x,
            "piece_y": piece_y,
            "piece_size": piece_size,
            "tolerance": settings["tolerance"],
        },
        ttl=settings["expires_seconds"],
    )
    return {
        "enabled": True,
        "challenge_id": challenge_id,
        "title": settings["title"],
        "description": settings["description"],
        "image": image,
        "shape": settings["shape"],
        "target_x": target_x,
        "piece_y": piece_y,
        "piece_size": piece_size,
        "expires_seconds": settings["expires_seconds"],
    }


async def verify_slider_challenge(challenge_id: str, x: int, scope: str) -> dict[str, Any]:
    if scope not in SLIDER_CAPTCHA_SCOPES:
        raise HTTPException(status_code=400, detail="滑块验证场景无效")
    challenge_key = f"{SLIDER_CAPTCHA_CHALLENGE_PREFIX}{challenge_id}"
    challenge = await cache_get_json(challenge_key)
    if not challenge:
        raise HTTPException(status_code=400, detail="滑块验证已过期，请重试")
    await cache_delete(challenge_key)
    target_x = int(challenge.get("target_x") or 0)
    tolerance = int(challenge.get("tolerance") or DEFAULT_SLIDER_CAPTCHA_SETTINGS["tolerance"])
    if abs(int(x) - target_x) > tolerance:
        raise HTTPException(status_code=400, detail="滑块位置不正确，请重试")

    ticket = uuid4().hex
    await cache_set_json(
        f"{SLIDER_CAPTCHA_TICKET_PREFIX}{ticket}",
        {"scope": scope},
        ttl=120,
    )
    return {"verified": True, "ticket": ticket}


async def consume_slider_ticket(db: AsyncSession, ticket: str, scope: str) -> None:
    settings = await get_slider_captcha_settings(db)
    if not settings["enabled"]:
        return
    if scope not in SLIDER_CAPTCHA_SCOPES or not ticket:
        raise HTTPException(status_code=400, detail="请先完成滑块验证")
    ticket_key = f"{SLIDER_CAPTCHA_TICKET_PREFIX}{ticket}"
    payload = await cache_get_json(ticket_key)
    if not payload:
        raise HTTPException(status_code=400, detail="滑块验证已失效，请重新验证")
    await cache_delete(ticket_key)
    if payload.get("scope") != scope:
        raise HTTPException(status_code=400, detail="滑块验证场景不匹配，请重试")
