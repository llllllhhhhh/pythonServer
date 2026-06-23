from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public import PUBLIC_CONFIG_KEY
from app.core.auth import hash_password
from app.core.cache import cache_delete
from app.core.database import get_db
from app.core.security import require_admin
from app.models import (
    DecorationConfig,
    InviteRelation,
    PlatformAnnouncement,
    PointRule,
    PreferenceEvent,
    SupportConversation,
    TravelOrder,
    TravelRoute,
    UserAccount,
    UserSession,
)
from app.schemas.api import (
    AdminUserRow,
    AdminUsersResponse,
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
    DecorationPayload,
    DecorationResponse,
    InviteOut,
    OrderOut,
    OrderReview,
    PasswordResetPayload,
    PointRuleOut,
    PointRulePayload,
    RegistrationReviewPayload,
    RegistrationRow,
    RouteCreate,
    RouteOut,
    RouteUpdate,
    StatusUpdatePayload,
    UserSummaryOut,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def build_user_row(user: UserAccount, user_conversations: list[SupportConversation]) -> AdminUserRow:
    return AdminUserRow(
        id=user.id,
        user_no=user.user_no,
        phone=user.phone,
        nickname=user.nickname,
        role=user.role,
        status=user.status,
        is_registered=user.is_registered,
        points=user.points,
        exam_status=user.exam_status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        conversation_count=len(user_conversations),
        open_conversation=any(item.status == "open" for item in user_conversations),
    )


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    routes = await db.scalar(select(func.count(TravelRoute.id)))
    pending_orders = await db.scalar(select(func.count(TravelOrder.id)).where(TravelOrder.status == 0))
    abnormal_invites = await db.scalar(select(func.count(InviteRelation.id)).where(InviteRelation.abnormal.is_(True)))
    total_users = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "user"))
    return {
        "routes": routes or 0,
        "pending_orders": pending_orders or 0,
        "abnormal_invites": abnormal_invites or 0,
        "users": total_users or 0,
    }


@router.get("/decoration/draft", response_model=DecorationResponse)
async def get_draft(db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(DecorationConfig).where(DecorationConfig.status == "draft").order_by(DecorationConfig.version.desc()))
    if not item:
        return DecorationResponse(status="draft", content={})
    return DecorationResponse(id=item.id, version=item.version, status=item.status, content=item.content, published_at=item.published_at)


@router.put("/decoration/draft", response_model=DecorationResponse)
async def save_draft(payload: DecorationPayload, db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(DecorationConfig).where(DecorationConfig.status == "draft").order_by(DecorationConfig.version.desc()))
    if item:
        item.content = payload.model_dump(mode="json")
        item.version += 1
    else:
        item = DecorationConfig(status="draft", version=1, content=payload.model_dump(mode="json"))
        db.add(item)
    await db.commit()
    await db.refresh(item)
    return DecorationResponse(id=item.id, version=item.version, status=item.status, content=item.content)


@router.post("/decoration/publish", response_model=DecorationResponse)
async def publish_decoration(payload: DecorationPayload, db: AsyncSession = Depends(get_db)):
    last_version = await db.scalar(select(func.max(DecorationConfig.version))) or 0
    item = DecorationConfig(
        status="published",
        version=last_version + 1,
        content=payload.model_dump(mode="json"),
        published_at=datetime.now(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await cache_delete(PUBLIC_CONFIG_KEY)
    return DecorationResponse(
        id=item.id,
        version=item.version,
        status=item.status,
        content=item.content,
        published_at=item.published_at,
    )


@router.get("/routes", response_model=list[RouteOut])
async def routes(db: AsyncSession = Depends(get_db)):
    return list(await db.scalars(select(TravelRoute).order_by(TravelRoute.id.desc())))


@router.post("/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def create_route(payload: RouteCreate, db: AsyncSession = Depends(get_db)):
    item = TravelRoute(**payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/routes/{route_id}", response_model=RouteOut)
async def update_route(route_id: int, payload: RouteUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelRoute, route_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/routes/{route_id}/status", response_model=RouteOut)
async def toggle_route(route_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelRoute, route_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route not found")
    item.status = enabled
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/announcements", response_model=list[AnnouncementOut])
async def announcements(db: AsyncSession = Depends(get_db)):
    return list(
        await db.scalars(
            select(PlatformAnnouncement).order_by(
                PlatformAnnouncement.pinned.desc(),
                PlatformAnnouncement.updated_at.desc(),
                PlatformAnnouncement.id.desc(),
            )
        )
    )


@router.post("/announcements", response_model=AnnouncementOut, status_code=status.HTTP_201_CREATED)
async def create_announcement(payload: AnnouncementCreate, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    if data["status"] and not data["published_at"]:
        data["published_at"] = datetime.now()
    item = PlatformAnnouncement(**data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.put("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(announcement_id: int, payload: AnnouncementUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    if item.status and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/announcements/{announcement_id}/status", response_model=AnnouncementOut)
async def toggle_announcement(announcement_id: int, enabled: bool, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    item.status = enabled
    if enabled and not item.published_at:
        item.published_at = datetime.now()
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/announcements/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(announcement_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(PlatformAnnouncement, announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Announcement not found")
    await db.delete(item)
    await db.commit()


@router.get("/points/rule", response_model=PointRuleOut)
async def get_point_rule(db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        raise HTTPException(status_code=404, detail="Point rule not found")
    return rule


@router.put("/points/rule", response_model=PointRuleOut)
async def update_point_rule(payload: PointRulePayload, db: AsyncSession = Depends(get_db)):
    rule = await db.get(PointRule, 1)
    if not rule:
        rule = PointRule(id=1, **payload.model_dump())
        db.add(rule)
    else:
        for key, value in payload.model_dump().items():
            setattr(rule, key, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get("/orders", response_model=list[OrderOut])
async def orders(order_status: int | None = Query(default=None, alias="status"), db: AsyncSession = Depends(get_db)):
    query = select(TravelOrder).order_by(TravelOrder.id.desc())
    if order_status is not None:
        query = query.where(TravelOrder.status == order_status)
    return list(await db.scalars(query))


@router.patch("/orders/{order_id}/review", response_model=OrderOut)
async def review_order(order_id: int, payload: OrderReview, db: AsyncSession = Depends(get_db)):
    item = await db.get(TravelOrder, order_id)
    if not item:
        raise HTTPException(status_code=404, detail="Order not found")
    item.status = payload.status
    item.reject_reason = payload.reject_reason
    if payload.agency is not None:
        item.agency = payload.agency
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/invites", response_model=list[InviteOut])
async def invites(abnormal: bool | None = None, db: AsyncSession = Depends(get_db)):
    query = select(InviteRelation).order_by(InviteRelation.id.desc())
    if abnormal is not None:
        query = query.where(InviteRelation.abnormal.is_(abnormal))
    return list(await db.scalars(query))


@router.patch("/invites/{invite_id}/freeze", response_model=InviteOut)
async def freeze_invite(invite_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(InviteRelation, invite_id)
    if not item:
        raise HTTPException(status_code=404, detail="Invite not found")
    item.frozen = True
    item.score_granted = False
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/users", response_model=AdminUsersResponse)
async def users(registered_only: bool = False, db: AsyncSession = Depends(get_db)):
    query = select(UserAccount).order_by(UserAccount.created_at.desc())
    if registered_only:
        query = query.where(UserAccount.is_registered.is_(True))
    all_users = list(await db.scalars(query))
    conversations = list(await db.scalars(select(SupportConversation)))
    conv_map: dict[str, list[SupportConversation]] = {}
    for item in conversations:
        conv_map.setdefault(item.user_id, []).append(item)
    rows = []
    for user in all_users:
        if user.role != "user":
            continue
        user_conversations = conv_map.get(user.user_no, [])
        rows.append(build_user_row(user, user_conversations))
    admins = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "admin")) or 0
    users_count = await db.scalar(select(func.count(UserAccount.id)).where(UserAccount.role == "user")) or 0
    registered = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.is_registered.is_(True))
    ) or 0
    active = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.status == "active")
    ) or 0
    pending = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.role == "user", UserAccount.status == "pending")
    ) or 0
    return AdminUsersResponse(
        summary=UserSummaryOut(
            total_users=users_count,
            registered_users=registered,
            admin_users=admins,
            active_users=active,
            pending_users=pending,
        ),
        users=rows,
    )


@router.get("/registrations", response_model=list[RegistrationRow])
async def registrations(db: AsyncSession = Depends(get_db)):
    query = (
        select(UserAccount)
        .where(UserAccount.role == "user", UserAccount.status == "pending")
        .order_by(UserAccount.created_at.desc())
    )
    return list(await db.scalars(query))


@router.patch("/registrations/{user_id}/review", response_model=AdminUserRow)
async def review_registration(user_id: int, payload: RegistrationReviewPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = "active" if payload.approved else "rejected"
    user.is_registered = bool(payload.approved)
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations)


@router.patch("/users/{user_id}/status", response_model=AdminUserRow)
async def update_user_status(user_id: int, payload: StatusUpdatePayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = payload.status
    user.is_registered = payload.status == "active"
    if payload.status in {"disabled", "cancelled", "rejected"}:
        await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations)


@router.patch("/users/{user_id}/password", response_model=AdminUserRow)
async def reset_user_password(user_id: int, payload: PasswordResetPayload, db: AsyncSession = Depends(get_db)):
    user = await db.get(UserAccount, user_id)
    if not user or user.role != "user":
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(payload.password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.role == user.role))
    await db.commit()
    await db.refresh(user)
    conversations = list(await db.scalars(select(SupportConversation).where(SupportConversation.user_id == user.user_no)))
    return build_user_row(user, conversations)


@router.get("/preferences")
async def preference_insights(db: AsyncSession = Depends(get_db)):
    events = list(await db.scalars(select(PreferenceEvent).order_by(PreferenceEvent.created_at.desc())))
    grouped: dict[tuple[str, str, str], dict] = {}
    users: dict[str, dict] = {}
    for event in events:
        key = (event.user_id, event.preference_type, event.target_key)
        item = grouped.setdefault(
            key,
            {
                "user_id": event.user_id,
                "user_name": event.user_name,
                "preference_type": event.preference_type,
                "target_key": event.target_key,
                "target_name": event.target_name,
                "score": 0,
                "actions": 0,
                "updated_at": event.created_at,
            },
        )
        item["score"] += event.score
        item["actions"] += 1
        if event.created_at > item["updated_at"]:
            item["updated_at"] = event.created_at
        user = users.setdefault(
            event.user_id,
            {
                "user_id": event.user_id,
                "user_name": event.user_name,
                "route_score": 0,
                "study_score": 0,
                "actions": 0,
                "updated_at": event.created_at,
                "interests": [],
            },
        )
        user[f"{event.preference_type}_score"] += event.score
        user["actions"] += 1
        if event.created_at > user["updated_at"]:
            user["updated_at"] = event.created_at

    active = [item for item in grouped.values() if item["score"] > 0]
    for item in active:
        users[item["user_id"]]["interests"].append(item)
    user_rows = []
    for user in users.values():
        interests = sorted(user.pop("interests"), key=lambda x: x["score"], reverse=True)
        top = interests[0] if interests else None
        user["top_interest"] = top["target_name"] if top else "暂无明确偏好"
        user["top_type"] = top["preference_type"] if top else ""
        user["interest_score"] = max(user["route_score"], 0) + max(user["study_score"], 0)
        user_rows.append(user)

    heat: dict[tuple[str, str], dict] = {}
    for item in active:
        key = (item["preference_type"], item["target_name"])
        rank = heat.setdefault(
            key,
            {"type": item["preference_type"], "name": item["target_name"], "score": 0, "users": set()},
        )
        rank["score"] += item["score"]
        rank["users"].add(item["user_id"])
    rankings = sorted([{**item, "users": len(item["users"])} for item in heat.values()], key=lambda x: x["score"], reverse=True)
    return {
        "summary": {
            "users": len(users),
            "events": len(events),
            "route_score": sum(max(u["route_score"], 0) for u in users.values()),
            "study_score": sum(max(u["study_score"], 0) for u in users.values()),
        },
        "rankings": rankings,
        "users": sorted(user_rows, key=lambda x: x["interest_score"], reverse=True),
    }
