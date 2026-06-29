from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import settings
from app.core.database import get_db
from app.core.storage import load_upload_bytes
from app.core.upload_settings import get_upload_setting
from app.models import ContentArticle, DecorationConfig, PlatformAnnouncement, PointRule, PreferenceEvent, SchoolSite, TravelRoute, UploadedAsset
from app.schemas.api import AnnouncementOut, ArticleOut, PointRuleOut, PreferenceEventPayload, RouteOut, SchoolSiteOut

router = APIRouter(prefix="/public", tags=["user-public"])
PUBLIC_CONFIG_KEY = "xuetuxing:decoration:published"
IMAGE_CACHE_HEADERS = {"Cache-Control": "public, max-age=2592000, immutable"}


def image_response(content: bytes, content_type: str, cache_key: str) -> Response:
    return Response(
        content=content,
        media_type=content_type,
        headers={**IMAGE_CACHE_HEADERS, "ETag": f'"{cache_key}"'},
    )


def build_thumbnail(content: bytes, max_width: int = 360) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None


async def load_upload_bytes_with_fallback(object_key: str, storage: str | None = None):
    primary = (storage or settings.storage_driver).lower()
    try:
        return await load_upload_bytes(object_key, primary)
    except Exception as original_error:
        fallback = "obs" if primary != "obs" else "local"
        try:
            return await load_upload_bytes(object_key, fallback)
        except Exception:
            raise original_error
    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            if image.width > max_width:
                ratio = max_width / float(image.width)
                image = image.resize((max_width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA", "P"}:
                if image.mode == "P":
                    image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=72, optimize=True)
            return output.getvalue()
    except Exception:
        return None


@router.get("/config")
async def published_config(
    response: Response,
    fresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    cached = None if fresh else await cache_get_json(PUBLIC_CONFIG_KEY)
    if cached:
        return {"source": "redis", **cached}
    item = await db.scalar(
        select(DecorationConfig)
        .where(DecorationConfig.status == "published")
        .order_by(DecorationConfig.version.desc())
    )
    if not item:
        raise HTTPException(status_code=404, detail="No published decoration config")
    payload = {
        "version": item.version,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "content": item.content,
    }
    if not fresh:
        await cache_set_json(PUBLIC_CONFIG_KEY, payload, ttl=600)
    return {"source": "mysql", **payload}


@router.get("/routes", response_model=list[RouteOut])
async def public_routes(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(TravelRoute).where(TravelRoute.status.is_(True)).order_by(TravelRoute.id.desc())
    )
    return list(result)


@router.get("/announcements", response_model=list[AnnouncementOut])
async def public_announcements(db: AsyncSession = Depends(get_db)):
    now = datetime.now()
    result = await db.scalars(
        select(PlatformAnnouncement)
        .where(
            PlatformAnnouncement.status.is_(True),
            or_(PlatformAnnouncement.start_at.is_(None), PlatformAnnouncement.start_at <= now),
            or_(PlatformAnnouncement.end_at.is_(None), PlatformAnnouncement.end_at >= now),
        )
        .order_by(
            PlatformAnnouncement.pinned.desc(),
            PlatformAnnouncement.published_at.desc(),
            PlatformAnnouncement.id.desc(),
        )
    )
    return list(result)


@router.get("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def public_announcement_detail(announcement_id: int, db: AsyncSession = Depends(get_db)):
    now = datetime.now()
    item = await db.scalar(
        select(PlatformAnnouncement).where(
            PlatformAnnouncement.id == announcement_id,
            PlatformAnnouncement.status.is_(True),
            or_(PlatformAnnouncement.start_at.is_(None), PlatformAnnouncement.start_at <= now),
            or_(PlatformAnnouncement.end_at.is_(None), PlatformAnnouncement.end_at >= now),
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item


@router.get("/articles", response_model=list[ArticleOut])
async def public_articles(category: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(ContentArticle).where(ContentArticle.status.is_(True))
    if category:
        query = query.where(ContentArticle.category == category)
    result = await db.scalars(
        query.order_by(
            ContentArticle.pinned.desc(),
            ContentArticle.sort_order.asc(),
            ContentArticle.published_at.desc(),
            ContentArticle.id.desc(),
        )
    )
    return list(result)


@router.get("/articles/{slug_or_id}", response_model=ArticleOut)
async def public_article_detail(slug_or_id: str, db: AsyncSession = Depends(get_db)):
    condition = ContentArticle.slug == slug_or_id
    if slug_or_id.isdigit():
        condition = or_(ContentArticle.id == int(slug_or_id), ContentArticle.slug == slug_or_id)
    item = await db.scalar(
        select(ContentArticle).where(
            condition,
            ContentArticle.status.is_(True),
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Article not found")
    return item


@router.get("/schools", response_model=list[SchoolSiteOut])
async def public_schools(keyword: str | None = None, db: AsyncSession = Depends(get_db)):
    query = select(SchoolSite).where(
        SchoolSite.status.is_(True),
        SchoolSite.review_status == "approved",
    )
    if keyword:
        like = f"%{keyword}%"
        query = query.where(
            or_(
                SchoolSite.name.like(like),
                SchoolSite.short_name.like(like),
                SchoolSite.city.like(like),
                SchoolSite.district.like(like),
            )
        )
    result = await db.scalars(
        query.order_by(
            SchoolSite.current.desc(),
            SchoolSite.sort_order.asc(),
            SchoolSite.id.desc(),
        )
    )
    return list(result)


@router.get("/points/rule", response_model=PointRuleOut)
async def public_point_rule(db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        raise HTTPException(status_code=404, detail="Point rule not configured")
    return rule


@router.get("/upload/settings")
async def public_upload_settings(db: AsyncSession = Depends(get_db)):
    return await get_upload_setting(db)


@router.post("/preferences/events", status_code=201)
async def record_preference(payload: PreferenceEventPayload, db: AsyncSession = Depends(get_db)):
    event = PreferenceEvent(**payload.model_dump())
    db.add(event)
    await db.commit()
    return {"ok": True, "id": event.id}


@router.get("/assets/{asset_id}/file")
async def public_asset_file(asset_id: int, db: AsyncSession = Depends(get_db)):
    asset = await db.get(UploadedAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    loaded = await load_upload_bytes_with_fallback(asset.object_key, asset.storage)
    cache_key = f"asset-{asset.id}-{asset.updated_at.timestamp() if asset.updated_at else asset.id}"
    return image_response(loaded.content, loaded.content_type, cache_key)


@router.get("/assets/{asset_id}/thumb")
async def public_asset_thumb(asset_id: int, db: AsyncSession = Depends(get_db)):
    asset = await db.get(UploadedAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    cache_dir = Path(settings.upload_dir) / ".thumb-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{asset_id}.jpg"
    cache_key = f"thumb-{asset.id}-{asset.updated_at.timestamp() if asset.updated_at else asset.id}"
    if cache_file.exists():
        return image_response(cache_file.read_bytes(), "image/jpeg", cache_key)
    loaded = await load_upload_bytes_with_fallback(asset.object_key, asset.storage)
    thumb = build_thumbnail(loaded.content)
    if not thumb:
        return image_response(loaded.content, loaded.content_type, cache_key)
    cache_file.write_bytes(thumb)
    return image_response(thumb, "image/jpeg", cache_key)


@router.get("/assets/object-thumb/{object_key:path}")
async def public_asset_object_thumb(object_key: str):
    suffix = object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
    if suffix not in {"jpg", "jpeg", "png", "gif", "webp"}:
        raise HTTPException(status_code=400, detail="仅支持图片预览")
    cache_dir = Path(settings.upload_dir) / ".thumb-cache" / "objects"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{abs(hash(object_key))}.jpg"
    cache_key = f"object-thumb-{abs(hash(object_key))}"
    if cache_file.exists():
        return image_response(cache_file.read_bytes(), "image/jpeg", cache_key)
    loaded = await load_upload_bytes_with_fallback(object_key, "obs" if settings.storage_driver.lower() == "obs" else "local")
    thumb = build_thumbnail(loaded.content)
    if not thumb:
        return image_response(loaded.content, loaded.content_type, cache_key)
    cache_file.write_bytes(thumb)
    return image_response(thumb, "image/jpeg", cache_key)


@router.get("/assets/object/{object_key:path}")
async def public_asset_object(object_key: str):
    suffix = object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
    if suffix not in {"jpg", "jpeg", "png", "gif", "webp"}:
        raise HTTPException(status_code=400, detail="仅支持图片预览")
    loaded = await load_upload_bytes_with_fallback(object_key, "obs" if settings.storage_driver.lower() == "obs" else "local")
    return image_response(loaded.content, loaded.content_type, f"object-{abs(hash(object_key))}")
