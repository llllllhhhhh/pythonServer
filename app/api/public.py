from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get_json, cache_set_json
from app.core.database import get_db
from app.models import DecorationConfig, PlatformAnnouncement, PointRule, PreferenceEvent, TravelRoute
from app.schemas.api import AnnouncementOut, PointRuleOut, PreferenceEventPayload, RouteOut

router = APIRouter(prefix="/public", tags=["user-public"])
PUBLIC_CONFIG_KEY = "xuetuxing:decoration:published"


@router.get("/config")
async def published_config(db: AsyncSession = Depends(get_db)):
    cached = await cache_get_json(PUBLIC_CONFIG_KEY)
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


@router.get("/points/rule", response_model=PointRuleOut)
async def public_point_rule(db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        raise HTTPException(status_code=404, detail="Point rule not configured")
    return rule


@router.post("/preferences/events", status_code=201)
async def record_preference(payload: PreferenceEventPayload, db: AsyncSession = Depends(get_db)):
    event = PreferenceEvent(**payload.model_dump())
    db.add(event)
    await db.commit()
    return {"ok": True, "id": event.id}
