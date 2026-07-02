from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

CONTRACT_SETTING_KEY = "travel.contract_template"
DEFAULT_TRAVEL_DATE_DAYS = 30

DEFAULT_CONTRACT_CONTENT = """甲方：{signer_name}
联系电话：{signer_phone}
证件号码：{id_no}

乙方：{agency}
订单编号：{order_no}
旅行产品：{title}
出行日期：{travel_date}

一、甲方确认已了解本次旅行服务的行程安排、费用说明、出行日期及平台规则。
二、乙方按订单约定提供旅行咨询、资源协调、出行服务对接及必要的售后协助。
三、甲方应保证签署信息真实有效，并按平台提示完成出行前必要确认。
四、本合同以电子签名方式签署，提交平台审核通过后正式生效。
五、如需变更或取消，应以平台公布规则及双方确认结果为准。"""


def build_travel_date_options(days: int = DEFAULT_TRAVEL_DATE_DAYS) -> list[str]:
    days = max(0, min(365, int(days or DEFAULT_TRAVEL_DATE_DAYS)))
    today = date.today()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


def normalize_contract_template(value: dict | None = None) -> dict:
    value = value if isinstance(value, dict) else {}
    title = str(value.get("title") or "旅行服务合同").strip()[:120] or "旅行服务合同"
    content = str(value.get("content") or DEFAULT_CONTRACT_CONTENT).strip() or DEFAULT_CONTRACT_CONTENT
    try:
        days = int(value.get("travel_date_days", DEFAULT_TRAVEL_DATE_DAYS))
    except (TypeError, ValueError):
        raw_options = value.get("travel_date_options")
        days = max(len(raw_options) - 1, DEFAULT_TRAVEL_DATE_DAYS) if isinstance(raw_options, list) else DEFAULT_TRAVEL_DATE_DAYS
    days = max(0, min(365, days))
    return {
        "title": title,
        "content": content,
        "travel_date_days": days,
        "travel_date_options": build_travel_date_options(days),
    }


async def get_contract_template(db: AsyncSession) -> dict:
    item = await db.get(SystemSetting, CONTRACT_SETTING_KEY)
    return normalize_contract_template(item.value if item else None)


async def save_contract_template(db: AsyncSession, payload: dict) -> dict:
    normalized = normalize_contract_template(payload)
    item = await db.get(SystemSetting, CONTRACT_SETTING_KEY)
    if item:
        item.value = normalized
        item.remark = "旅行合同模板"
    else:
        item = SystemSetting(key=CONTRACT_SETTING_KEY, value=normalized, remark="旅行合同模板")
        db.add(item)
    await db.commit()
    return normalized
