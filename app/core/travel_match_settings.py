from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting


TRAVEL_MATCH_SETTING_KEY = "travel.match_settings"

DEFAULT_TRAVEL_MATCH_SETTINGS: dict[str, Any] = {
    "destinations": [
        {"label": "西南秘境", "keywords": ["西南", "川西", "四川", "成都", "云南", "贵州", "重庆", "雪山", "秘境", "藏", "高原"], "weight": 38, "enabled": True},
        {"label": "海滨城市", "keywords": ["海滨", "海风", "海岸", "青岛", "泉州", "厦门", "三亚", "海岛", "滨海", "海"], "weight": 38, "enabled": True},
        {"label": "西北旷野", "keywords": ["西北", "青海", "甘肃", "新疆", "敦煌", "沙漠", "戈壁", "旷野", "祁连"], "weight": 38, "enabled": True},
        {"label": "江南古镇", "keywords": ["江南", "古镇", "苏州", "杭州", "乌镇", "水乡", "徽州", "绍兴"], "weight": 38, "enabled": True},
    ],
    "themes": [
        {"label": "户外徒步", "keywords": ["户外", "徒步", "雪山", "轻徒步", "山", "露营"], "weight": 28, "enabled": True},
        {"label": "人文研学", "keywords": ["人文", "研学", "非遗", "文化", "博物", "知行"], "weight": 28, "enabled": True},
        {"label": "美食探索", "keywords": ["美食", "小吃", "烟火", "味道", "非遗"], "weight": 28, "enabled": True},
        {"label": "旅拍出片", "keywords": ["旅拍", "出片", "摄影", "海风", "雪山", "古镇"], "weight": 28, "enabled": True},
        {"label": "毕业团建", "keywords": ["毕业", "团建", "青年", "假日", "同学"], "weight": 28, "enabled": True},
        {"label": "亲子同行", "keywords": ["亲子", "家庭", "孩子", "研学"], "weight": 28, "enabled": True},
    ],
    "weights": {
        "destination_miss_penalty": -18,
        "theme_miss_penalty": -6,
        "day_match": 20,
        "day_near": 8,
        "day_miss_penalty": -8,
        "budget_good": 18,
        "budget_ok": 13,
        "budget_low": 7,
        "budget_over_near": 3,
        "budget_over_penalty": -22,
        "stock_available": 8,
        "stock_empty_penalty": -24,
        "display_weight_cap": 6,
        "min_score": 42,
    },
}


def _normalize_terms(items: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = items if isinstance(items, list) else []
    normalized: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        keywords = item.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [part.strip() for part in keywords.replace("，", ",").split(",")]
        keywords = [str(keyword).strip()[:30] for keyword in keywords if str(keyword).strip()]
        normalized.append(
            {
                "label": label[:30],
                "keywords": keywords[:30],
                "weight": max(0, min(100, int(item.get("weight") or 0))),
                "enabled": item.get("enabled") is not False,
            }
        )
    return normalized or deepcopy(fallback)


def normalize_match_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    defaults = deepcopy(DEFAULT_TRAVEL_MATCH_SETTINGS)
    weights = defaults["weights"]
    incoming_weights = raw.get("weights") if isinstance(raw.get("weights"), dict) else {}
    for key, default_value in weights.items():
        try:
            weights[key] = int(incoming_weights.get(key, default_value))
        except (TypeError, ValueError):
            weights[key] = default_value
    return {
        "destinations": _normalize_terms(raw.get("destinations"), defaults["destinations"]),
        "themes": _normalize_terms(raw.get("themes"), defaults["themes"]),
        "weights": weights,
    }


async def get_travel_match_settings(db: AsyncSession) -> dict[str, Any]:
    item = await db.get(SystemSetting, TRAVEL_MATCH_SETTING_KEY)
    return normalize_match_settings(item.value if item else None)


async def save_travel_match_settings(db: AsyncSession, value: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_match_settings(value)
    item = await db.get(SystemSetting, TRAVEL_MATCH_SETTING_KEY)
    if item:
        item.value = normalized
        item.remark = "智能定制匹配词库和权重配置"
    else:
        item = SystemSetting(
            key=TRAVEL_MATCH_SETTING_KEY,
            value=normalized,
            remark="智能定制匹配词库和权重配置",
        )
        db.add(item)
    await db.commit()
    return normalized
