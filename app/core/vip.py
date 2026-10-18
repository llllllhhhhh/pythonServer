from datetime import datetime

VIP_PLANS = {
    1: {"name": "周度会员", "vip_type": "周度 VIP", "price": 9, "duration_days": 7, "note": "灵活短期体验"},
    2: {"name": "月度会员", "vip_type": "月度 VIP", "price": 12, "duration_days": 30, "note": "低至 0.4 元 / 天"},
    3: {"name": "年度会员", "vip_type": "年度 VIP", "price": 88, "duration_days": 365, "note": "全年权益更划算"},
}


def is_vip_active(user) -> bool:
    if not bool(getattr(user, "vip_enabled", False)):
        return False
    expires_at = getattr(user, "vip_expires_at", None)
    return not expires_at or expires_at > datetime.now()


def get_vip_plan(plan_id: int) -> dict | None:
    return VIP_PLANS.get(int(plan_id))
