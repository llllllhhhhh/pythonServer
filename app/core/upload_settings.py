from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

UPLOAD_SETTING_KEY = "upload.image"
DEFAULT_MAX_IMAGE_MB = 8
MIN_IMAGE_MB = 1
MAX_IMAGE_MB = 50


def normalize_max_image_mb(value: int | float | str | None) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = DEFAULT_MAX_IMAGE_MB
    return max(MIN_IMAGE_MB, min(MAX_IMAGE_MB, number))


async def get_upload_setting(db: AsyncSession) -> dict:
    item = await db.get(SystemSetting, UPLOAD_SETTING_KEY)
    value = item.value if item and isinstance(item.value, dict) else {}
    max_image_mb = normalize_max_image_mb(value.get("max_image_mb"))
    return {
        "max_image_mb": max_image_mb,
        "max_image_bytes": max_image_mb * 1024 * 1024,
    }


async def save_upload_setting(db: AsyncSession, max_image_mb: int) -> dict:
    max_image_mb = normalize_max_image_mb(max_image_mb)
    payload = {"max_image_mb": max_image_mb}
    item = await db.get(SystemSetting, UPLOAD_SETTING_KEY)
    if item:
        item.value = payload
        item.remark = "全局图片上传大小限制"
    else:
        item = SystemSetting(key=UPLOAD_SETTING_KEY, value=payload, remark="全局图片上传大小限制")
        db.add(item)
    await db.commit()
    return await get_upload_setting(db)
