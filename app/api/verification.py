from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.storage import save_upload_bytes
from app.core.upload_settings import get_upload_setting
from app.models import GraduationCertification, UploadedAsset, UserAccount
from app.schemas.api import GraduationCertificationOut

router = APIRouter(prefix="/verification", tags=["verification"])

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/graduation", response_model=GraduationCertificationOut | None)
async def my_graduation_certification(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await db.scalar(
        select(GraduationCertification).where(GraduationCertification.user_id == current_user.id)
    )


@router.post("/graduation", response_model=GraduationCertificationOut)
async def submit_graduation_certification(
    real_name: str = Form(...),
    school_name: str = Form(...),
    major_name: str = Form(default=""),
    graduation_date: str = Form(default=""),
    certificate_no: str = Form(default=""),
    file: UploadFile = File(...),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    real_name = real_name.strip()
    school_name = school_name.strip()
    if not real_name or len(real_name) > 60:
        raise HTTPException(status_code=400, detail="请填写正确的真实姓名")
    if not school_name or len(school_name) > 120:
        raise HTTPException(status_code=400, detail="请填写正确的录取院校")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="录取通知书仅支持 jpg、png、webp 图片")
    upload_setting = await get_upload_setting(db)
    max_file_size = upload_setting["max_image_bytes"]
    max_image_mb = upload_setting["max_image_mb"]
    content = await file.read(max_file_size + 1)
    if len(content) > max_file_size:
        raise HTTPException(status_code=400, detail=f"录取通知书图片不能超过 {max_image_mb}MB")

    item = await db.scalar(
        select(GraduationCertification).where(GraduationCertification.user_id == current_user.id)
    )
    if item and item.status == "pending":
        raise HTTPException(status_code=400, detail="录取通知书正在审核中，请勿重复提交")
    if not item:
        item = GraduationCertification(user_id=current_user.id)
        db.add(item)

    stored = await save_upload_bytes(content, file.filename, file.content_type, "graduation")
    asset = UploadedAsset(
        source="admission_notice",
        storage=stored.storage,
        bucket=stored.bucket,
        object_key=stored.object_key,
        path=stored.path,
        url=stored.url,
        filename=Path(stored.object_key).name,
        original_name=file.filename or "",
        mime_type=file.content_type or "",
        size=len(content),
        uploader_role="user",
        user_id=current_user.id,
    )
    db.add(asset)
    await db.flush()

    item.real_name = real_name
    item.school_name = school_name
    item.major_name = major_name.strip()[:120]
    item.graduation_date = graduation_date.strip()[:20]
    item.certificate_no = certificate_no.strip()[:80]
    item.certificate_image = f"{settings.api_prefix}/public/assets/{asset.id}/file"
    item.status = "pending"
    item.reject_reason = ""
    item.reviewed_by = None
    item.reviewed_at = None
    await db.commit()
    await db.refresh(item)
    return item
