from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting


VIP_PLAN_SETTING_KEY = "vip.plans"
DEFAULT_BENEFITS = [
    "解锁全部高级功能",
    "专属 VIP 身份标识",
    "会员有效期内持续享受会员权益",
]
DEFAULT_VIP_PLANS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "周度会员",
        "vip_type": "周度 VIP",
        "price": 9.0,
        "duration_days": 7,
        "note": "灵活短期体验",
        "badge": "体验",
        "benefits": DEFAULT_BENEFITS,
        "enabled": True,
        "sort_order": 10,
    },
    {
        "id": 2,
        "name": "月度会员",
        "vip_type": "月度 VIP",
        "price": 12.0,
        "duration_days": 30,
        "note": "低至 0.4 元 / 天",
        "badge": "推荐",
        "benefits": DEFAULT_BENEFITS,
        "enabled": True,
        "sort_order": 20,
    },
    {
        "id": 3,
        "name": "年度会员",
        "vip_type": "年度 VIP",
        "price": 88.0,
        "duration_days": 365,
        "note": "全年权益更划算",
        "badge": "超值",
        "benefits": DEFAULT_BENEFITS,
        "enabled": True,
        "sort_order": 30,
    },
]


def is_vip_active(user) -> bool:
    if not bool(getattr(user, "vip_enabled", False)):
        return False
    expires_at = getattr(user, "vip_expires_at", None)
    return not expires_at or expires_at > datetime.now()


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _price(value: Any, fallback: float) -> float:
    try:
        price = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        price = Decimal(str(fallback))
    return float(max(Decimal("0.01"), min(Decimal("99999.99"), price)))


def normalize_vip_plans(value: Any) -> list[dict[str, Any]]:
    raw = value.get("plans") if isinstance(value, dict) else value
    rows = raw if isinstance(raw, list) else deepcopy(DEFAULT_VIP_PLANS)
    defaults = {item["id"]: item for item in DEFAULT_VIP_PLANS}
    normalized: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        try:
            plan_id = int(item.get("id") or index + 1)
        except (TypeError, ValueError):
            continue
        if plan_id <= 0 or plan_id in used_ids:
            continue
        fallback = defaults.get(plan_id, {})
        name = _text(item.get("name") or fallback.get("name"), 40)
        vip_type = _text(item.get("vip_type") or fallback.get("vip_type") or name, 30)
        if not name or not vip_type:
            continue
        benefits_value = item.get("benefits")
        if isinstance(benefits_value, str):
            benefits_value = benefits_value.replace("；", "\n").splitlines()
        benefits = [_text(benefit, 100) for benefit in (benefits_value or fallback.get("benefits") or DEFAULT_BENEFITS)]
        benefits = [benefit for benefit in benefits if benefit][:12] or deepcopy(DEFAULT_BENEFITS)
        try:
            duration_days = max(1, min(3650, int(item.get("duration_days") or fallback.get("duration_days") or 30)))
            sort_order = max(0, min(9999, int(item.get("sort_order") or fallback.get("sort_order") or (index + 1) * 10)))
        except (TypeError, ValueError):
            duration_days = int(fallback.get("duration_days") or 30)
            sort_order = int(fallback.get("sort_order") or (index + 1) * 10)
        normalized.append(
            {
                "id": plan_id,
                "name": name,
                "vip_type": vip_type,
                "price": _price(item.get("price"), float(fallback.get("price") or 1)),
                "duration_days": duration_days,
                "note": _text(item.get("note") or fallback.get("note"), 100),
                "badge": _text(item.get("badge") or fallback.get("badge"), 16),
                "benefits": benefits,
                "enabled": item.get("enabled") is not False,
                "sort_order": sort_order,
            }
        )
        used_ids.add(plan_id)
    return sorted(normalized or deepcopy(DEFAULT_VIP_PLANS), key=lambda plan: (plan["sort_order"], plan["id"]))


async def get_vip_plans(db: AsyncSession, include_disabled: bool = False) -> list[dict[str, Any]]:
    item = await db.get(SystemSetting, VIP_PLAN_SETTING_KEY)
    plans = normalize_vip_plans(item.value if item else None)
    return plans if include_disabled else [plan for plan in plans if plan["enabled"]]


async def get_vip_plan(db: AsyncSession, plan_id: int, include_disabled: bool = False) -> dict[str, Any] | None:
    plans = await get_vip_plans(db, include_disabled=include_disabled)
    return next((plan for plan in plans if plan["id"] == int(plan_id)), None)


async def save_vip_plans(db: AsyncSession, value: Any) -> list[dict[str, Any]]:
    plans = normalize_vip_plans(value)
    item = await db.get(SystemSetting, VIP_PLAN_SETTING_KEY)
    payload = {"plans": plans}
    if item:
        item.value = payload
        item.remark = "VIP 套餐、价格、有效期与权益配置"
    else:
        item = SystemSetting(key=VIP_PLAN_SETTING_KEY, value=payload, remark="VIP 套餐、价格、有效期与权益配置")
        db.add(item)
    await db.commit()
    return plans
