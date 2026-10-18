from __future__ import annotations

from datetime import date, timedelta
import hashlib

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContractTemplateSnapshot, SystemSetting

CONTRACT_SETTING_KEY = "travel.contract_template"
DEFAULT_TRAVEL_DATE_DAYS = 30
MAX_TRAVEL_DATE_DAYS = 36500
DEFAULT_CONTRACT_TITLE = "旅行服务合同"

DEFAULT_CONTRACT_CONTENT = """甲方（出行人）：{signer_name}
联系电话：{signer_phone}
证件号码：{id_no}

乙方（服务方）：{agency}
订单编号：{order_no}
旅行产品：{title}
出行日期：{travel_date}
订单金额：{amount_text}

一、甲方确认已阅读并认可本订单对应的旅行产品、出行日期、服务内容及平台规则。
二、乙方按照订单约定提供旅行协调、接送安排及相关服务支持。
三、本合同采用电子签名方式签署，提交后进入平台审核，审核通过后正式生效。
四、如因不可抗力、政策变化、天气交通等原因影响出行，双方按平台规则协商调整或处理。
"""


def build_travel_date_options(days: int = DEFAULT_TRAVEL_DATE_DAYS) -> list[str]:
    days = max(0, min(MAX_TRAVEL_DATE_DAYS, int(days or DEFAULT_TRAVEL_DATE_DAYS)))
    today = date.today()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(min(days, 31) + 1)]


def normalize_contract_template(value: dict | None = None) -> dict:
    value = value if isinstance(value, dict) else {}
    title = str(value.get("title") or DEFAULT_CONTRACT_TITLE).strip()[:120] or DEFAULT_CONTRACT_TITLE
    content = str(value.get("content") or DEFAULT_CONTRACT_CONTENT).strip() or DEFAULT_CONTRACT_CONTENT
    unit = str(value.get("travel_date_unit") or "").strip().lower()
    try:
        raw_value = int(value.get("travel_date_value"))
    except (TypeError, ValueError):
        raw_value = 0
    if unit in {"day", "year"} and raw_value > 0:
        limit_value = max(1, min(100 if unit == "year" else MAX_TRAVEL_DATE_DAYS, raw_value))
        days = limit_value * 365 if unit == "year" else limit_value
    else:
        try:
            days = int(value.get("travel_date_days", DEFAULT_TRAVEL_DATE_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_TRAVEL_DATE_DAYS
        raw_options = value.get("travel_date_options")
        if not days and isinstance(raw_options, list):
            days = max(len(raw_options) - 1, DEFAULT_TRAVEL_DATE_DAYS)
        days = max(1, min(MAX_TRAVEL_DATE_DAYS, days))
        if days >= 365 and days % 365 == 0:
            unit, limit_value = "year", days // 365
        else:
            unit, limit_value = "day", days
    today = date.today()
    return {
        "title": title,
        "content": content,
        "travel_date_days": days,
        "travel_date_value": limit_value,
        "travel_date_unit": unit,
        "travel_date_start": today.isoformat(),
        "travel_date_end": (today + timedelta(days=days)).isoformat(),
        "travel_date_options": build_travel_date_options(days),
    }


def _template_hash(template: dict) -> str:
    payload = "\n".join([
        str(template.get("title") or ""),
        str(template.get("content") or ""),
        str(template.get("travel_date_days") or 0),
        str(template.get("travel_date_unit") or "day"),
        str(template.get("travel_date_value") or 0),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _with_snapshot_fields(template: dict, snapshot: ContractTemplateSnapshot | None) -> dict:
    version = int(snapshot.version) if snapshot else int(template.get("version") or 0)
    snapshot_id = int(snapshot.id) if snapshot else int(template.get("snapshot_id") or 0)
    return {
        **template,
        "version": version,
        "snapshot_id": snapshot_id,
        "contract_template_version": version,
        "contract_template_snapshot_id": snapshot_id,
    }


def _snapshot_to_template(snapshot: ContractTemplateSnapshot) -> dict:
    return _with_snapshot_fields(
        normalize_contract_template({
            "title": snapshot.title,
            "content": snapshot.content,
            "travel_date_days": snapshot.travel_date_days,
        }),
        snapshot,
    )


async def ensure_contract_template_snapshot(db: AsyncSession, template: dict | None = None) -> ContractTemplateSnapshot:
    normalized = normalize_contract_template(template)
    content_hash = _template_hash(normalized)
    latest = await db.scalar(select(ContractTemplateSnapshot).order_by(ContractTemplateSnapshot.version.desc()))
    if latest and latest.content_hash == content_hash:
        return latest
    if latest:
        existing = await db.scalar(select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.content_hash == content_hash))
        if existing:
            return existing
    next_version = int(await db.scalar(select(func.max(ContractTemplateSnapshot.version))) or 0) + 1
    snapshot = ContractTemplateSnapshot(
        version=next_version,
        title=normalized["title"],
        content=normalized["content"],
        travel_date_days=normalized["travel_date_days"],
        content_hash=content_hash,
    )
    db.add(snapshot)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent save computed the same next version; reload the
        # matching snapshot instead of failing or duplicating versions.
        await db.rollback()
        existing = await db.scalar(
            select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.content_hash == content_hash)
        )
        if existing:
            return existing
        snapshot = ContractTemplateSnapshot(
            version=int(await db.scalar(select(func.max(ContractTemplateSnapshot.version))) or 0) + 1,
            title=normalized["title"],
            content=normalized["content"],
            travel_date_days=normalized["travel_date_days"],
            content_hash=content_hash,
        )
        db.add(snapshot)
        await db.flush()
    return snapshot


async def get_contract_template(db: AsyncSession) -> dict:
    item = await db.get(SystemSetting, CONTRACT_SETTING_KEY)
    template = normalize_contract_template(item.value if item else None)
    version = int((item.value or {}).get("version") or 0) if item and isinstance(item.value, dict) else 0
    snapshot_id = int((item.value or {}).get("snapshot_id") or 0) if item and isinstance(item.value, dict) else 0
    snapshot = None
    if snapshot_id:
        snapshot = await db.get(ContractTemplateSnapshot, snapshot_id)
    if not snapshot and version:
        snapshot = await db.scalar(select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.version == version))
    if not snapshot:
        snapshot = await ensure_contract_template_snapshot(db, template)
        saved = _with_snapshot_fields(template, snapshot)
        if item:
            item.value = saved
            item.remark = "travel contract template"
        else:
            item = SystemSetting(key=CONTRACT_SETTING_KEY, value=saved, remark="travel contract template")
            db.add(item)
        await db.commit()
        await db.refresh(snapshot)
    return _with_snapshot_fields(template, snapshot)


async def get_contract_template_snapshot(db: AsyncSession, snapshot_id: int = 0, version: int = 0) -> dict | None:
    snapshot = None
    if snapshot_id:
        snapshot = await db.get(ContractTemplateSnapshot, snapshot_id)
    if not snapshot and version:
        snapshot = await db.scalar(select(ContractTemplateSnapshot).where(ContractTemplateSnapshot.version == version))
    return _snapshot_to_template(snapshot) if snapshot else None


async def save_contract_template(db: AsyncSession, payload: dict) -> dict:
    normalized = normalize_contract_template(payload)
    snapshot = await ensure_contract_template_snapshot(db, normalized)
    saved = _with_snapshot_fields(normalized, snapshot)
    item = await db.get(SystemSetting, CONTRACT_SETTING_KEY)
    if item:
        item.value = saved
        item.remark = "travel contract template"
    else:
        item = SystemSetting(key=CONTRACT_SETTING_KEY, value=saved, remark="travel contract template")
        db.add(item)
    await db.commit()
    await db.refresh(snapshot)
    return _with_snapshot_fields(normalized, snapshot)
