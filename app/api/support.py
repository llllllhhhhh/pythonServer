from __future__ import annotations

from io import BytesIO
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.security import get_current_user, get_optional_user, require_admin
from app.core.storage import save_upload_bytes
from app.core.upload_settings import get_upload_setting
from app.api.merchant import extract_bearer_token, get_current_school
from app.models import CommerceOrder, CommerceOrderItem, SchoolMerchantSession, SchoolSite, StudyOrder, SupportConversation, SupportMessage, UploadedAsset, UserAccount, UserSession
from app.schemas.api import SupportSessionPayload

router = APIRouter(prefix="/support", tags=["support"])

UPLOAD_ROOT = Path(settings.upload_dir) / "support"


def build_image_thumbnail(content: bytes, max_width: int = 360) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            if image.width <= max_width:
                return None
            ratio = max_width / float(image.width)
            target_size = (max_width, max(1, int(image.height * ratio)))
            image = image.resize(target_size, Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                background.paste(image, mask=image.split()[-1] if image.mode in {"RGBA", "LA"} else None)
                image = background
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=72, optimize=True)
            return output.getvalue()
    except Exception:
        return None


def message_dict(message: SupportMessage) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_role": message.sender_role,
        "sender_name": message.sender_name,
        "content": message.content,
        "message_type": message.message_type,
        "image_url": message.image_url,
        "image_thumb_url": message.image_thumb_url or "",
        "created_at": message.created_at.isoformat() if message.created_at else datetime.now().isoformat(),
    }


def conversation_dict(item: SupportConversation) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "user_name": item.user_name,
        "status": item.status,
        "last_message": item.last_message,
        "conversation_type": getattr(item, "conversation_type", "platform"),
        "order_id": getattr(item, "order_id", 0),
        "order_no": getattr(item, "order_no", ""),
        "product_id": getattr(item, "product_id", 0),
        "product_name": getattr(item, "product_name", ""),
        "school_id": getattr(item, "school_id", 0),
        "school_name": getattr(item, "school_name", ""),
        "unread_admin": item.unread_admin,
        "unread_user": item.unread_user,
        "unread_merchant": getattr(item, "unread_merchant", 0),
        "user_online": item.user_online,
        "admin_online": item.admin_online,
        "merchant_online": getattr(item, "merchant_online", False),
        "last_user_online_at": item.last_user_online_at.isoformat() if item.last_user_online_at else None,
        "last_admin_online_at": item.last_admin_online_at.isoformat() if item.last_admin_online_at else None,
        "last_merchant_online_at": item.last_merchant_online_at.isoformat() if getattr(item, "last_merchant_online_at", None) else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


class SupportConnectionManager:
    def __init__(self):
        self.connections: dict[str, dict[str, set[WebSocket]]] = defaultdict(lambda: {"user": set(), "admin": set(), "merchant": set()})

    async def connect(self, conversation_id: str, role: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[conversation_id][role].add(websocket)

    def disconnect(self, conversation_id: str, role: str, websocket: WebSocket):
        group = self.connections.get(conversation_id)
        if not group:
            return
        group[role].discard(websocket)
        if not group["user"] and not group["admin"] and not group["merchant"]:
            self.connections.pop(conversation_id, None)

    def is_online(self, conversation_id: str, role: str) -> bool:
        return bool(self.connections.get(conversation_id, {}).get(role))

    async def broadcast(self, conversation_id: str, payload: dict):
        stale: list[tuple[str, WebSocket]] = []
        group = self.connections.get(conversation_id, {})
        for role in ("user", "admin", "merchant"):
            for socket in group.get(role, set()).copy():
                try:
                    await socket.send_json(payload)
                except Exception:
                    stale.append((role, socket))
        for role, socket in stale:
            self.disconnect(conversation_id, role, socket)


manager = SupportConnectionManager()


async def sync_presence(conversation_id: str) -> None:
    async with SessionLocal() as db:
        conversation = await db.get(SupportConversation, conversation_id)
        if not conversation:
            return
        conversation.user_online = manager.is_online(conversation_id, "user")
        conversation.admin_online = manager.is_online(conversation_id, "admin")
        conversation.merchant_online = manager.is_online(conversation_id, "merchant")
        if conversation.user_online:
            conversation.last_user_online_at = datetime.now()
        if conversation.admin_online:
            conversation.last_admin_online_at = datetime.now()
        if conversation.merchant_online:
            conversation.last_merchant_online_at = datetime.now()
        await db.commit()


def _message_preview(content: str, message_type: str) -> str:
    if message_type == "image":
        return "[图片]"
    return content[:255]


@router.post("/conversations")
async def create_conversation(
    payload: dict = Body(...),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(payload.get("user_id") or "").strip()
    user_name = str(payload.get("user_name") or current_user.nickname or current_user.user_no).strip()
    if current_user.user_no != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    item = await db.scalar(
        select(SupportConversation)
        .where(SupportConversation.user_id == user_id, SupportConversation.conversation_type == "platform")
        .order_by(SupportConversation.updated_at.desc())
    )
    if not item:
        item = SupportConversation(id=str(uuid4()), user_id=user_id, user_name=user_name, conversation_type="platform")
        db.add(item)
    else:
        item.user_name = user_name
        item.status = "open"
    await db.commit()
    await db.refresh(item)
    return conversation_dict(item)


@router.post("/order-conversations")
async def create_order_conversation(
    payload: dict = Body(...),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(payload.get("user_id") or "").strip()
    user_name = str(payload.get("user_name") or current_user.nickname or current_user.user_no).strip()
    order_id = int(payload.get("order_id") or 0)
    order_no = str(payload.get("order_no") or "").strip()
    if current_user.user_no != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    query = select(StudyOrder).where(StudyOrder.user_id == current_user.id)
    if order_id:
        query = query.where(StudyOrder.id == order_id)
    elif order_no:
        query = query.where(StudyOrder.order_no == order_no)
    else:
        raise HTTPException(status_code=400, detail="missing order")
    order = await db.scalar(query)
    product_id = 0
    product_name = ""
    school_id = 0
    support_order_id = 0
    support_order_no = ""
    payment_status = ""
    if order:
        product_id = order.product_id
        product_name = order.product_name
        school_id = order.school_id
        support_order_id = order.id
        support_order_no = order.order_no
        payment_status = order.payment_status
    else:
        standard_query = select(CommerceOrder).where(CommerceOrder.user_id == current_user.id)
        if order_id:
            standard_query = standard_query.where(CommerceOrder.id == order_id)
        elif order_no:
            standard_query = standard_query.where(CommerceOrder.order_no == order_no)
        standard_order = await db.scalar(standard_query)
        if not standard_order:
            raise HTTPException(status_code=404, detail="study order not found")
        first_item = await db.scalar(
            select(CommerceOrderItem)
            .where(CommerceOrderItem.order_id == standard_order.id)
            .order_by(CommerceOrderItem.id)
        )
        product_id = first_item.product_id if first_item else 0
        product_name = first_item.product_name if first_item else "学习产品订单"
        school_id = standard_order.school_id or (first_item.school_id if first_item else 0)
        support_order_id = standard_order.id
        support_order_no = standard_order.order_no
        payment_status = standard_order.payment_status
    if payment_status != "paid":
        raise HTTPException(status_code=400, detail="paid order required")
    school = await db.get(SchoolSite, school_id) if school_id else None
    if not school:
        raise HTTPException(status_code=400, detail="school not bound")
    item = await db.scalar(
        select(SupportConversation).where(
            SupportConversation.conversation_type == "study_order",
            SupportConversation.order_id == support_order_id,
            SupportConversation.order_no == support_order_no,
            SupportConversation.user_id == user_id,
        )
    )
    if not item:
        item = SupportConversation(
            id=str(uuid4()),
            user_id=user_id,
            user_name=user_name,
            conversation_type="study_order",
            order_id=support_order_id,
            order_no=support_order_no,
            product_id=product_id,
            product_name=product_name,
            school_id=school_id,
            school_name=school.name,
            last_message="order support conversation created",
        )
        db.add(item)
    else:
        item.user_name = user_name
        item.status = "open"
        item.product_name = product_name
        item.school_name = school.name
    await db.commit()
    await db.refresh(item)
    return conversation_dict(item)


@router.get("/conversations/{conversation_id}/messages")
async def user_messages(
    conversation_id: str,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(SupportConversation, conversation_id)
    if not item or item.user_id != current_user.user_no:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = list(await db.scalars(select(SupportMessage).where(SupportMessage.conversation_id == conversation_id).order_by(SupportMessage.id)))
    item.unread_user = 0
    await db.commit()
    return [message_dict(message) for message in messages]


@router.get("/admin/conversations", dependencies=[Depends(require_admin)])
async def admin_conversations(db: AsyncSession = Depends(get_db)):
    items = list(await db.scalars(select(SupportConversation).order_by(SupportConversation.updated_at.desc())))
    return [conversation_dict(item) for item in items]


@router.get("/admin/conversations/{conversation_id}/messages", dependencies=[Depends(require_admin)])
async def admin_messages(conversation_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.get(SupportConversation, conversation_id)
    if not item:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = list(await db.scalars(select(SupportMessage).where(SupportMessage.conversation_id == conversation_id).order_by(SupportMessage.id)))
    item.unread_admin = 0
    await db.commit()
    return [message_dict(message) for message in messages]


@router.get("/merchant/conversations")
async def merchant_conversations(
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    items = list(
        await db.scalars(
            select(SupportConversation)
            .where(SupportConversation.school_id == school.id, SupportConversation.conversation_type == "study_order")
            .order_by(SupportConversation.updated_at.desc())
        )
    )
    return [conversation_dict(item) for item in items]


@router.get("/merchant/conversations/{conversation_id}/messages")
async def merchant_messages(
    conversation_id: str,
    school: SchoolSite = Depends(get_current_school),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(SupportConversation, conversation_id)
    if not item or item.school_id != school.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = list(await db.scalars(select(SupportMessage).where(SupportMessage.conversation_id == conversation_id).order_by(SupportMessage.id)))
    item.unread_merchant = 0
    await db.commit()
    return [message_dict(message) for message in messages]


@router.patch("/admin/conversations/{conversation_id}/close", dependencies=[Depends(require_admin)])
async def close_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.get(SupportConversation, conversation_id)
    if not item:
        raise HTTPException(status_code=404, detail="conversation not found")
    item.status = "closed"
    await db.commit()
    await db.refresh(item)
    await manager.broadcast(conversation_id, {"type": "status", "status": "closed"})
    return conversation_dict(item)


@router.post("/upload")
async def upload_support_image(
    request: Request,
    conversation_id: str = Form(...),
    role: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount | None = Depends(get_optional_user),
):
    if role not in {"user", "admin", "merchant"}:
        raise HTTPException(status_code=400, detail="unsupported upload role")
    if role == "admin":
        await require_admin(x_admin_key=request.headers.get("x-admin-key"), authorization=request.headers.get("authorization"), db=db)
    conversation = await db.get(SupportConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="conversation not found")
    if role == "user" and (not current_user or conversation.user_id != current_user.user_no):
        raise HTTPException(status_code=403, detail="upload forbidden")
    if role == "merchant":
        token = extract_bearer_token(request.headers.get("authorization"))
        session = await db.get(SchoolMerchantSession, token or "")
        if not session or session.expires_at < datetime.now() or session.school_id != conversation.school_id:
            raise HTTPException(status_code=403, detail="merchant upload forbidden")
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(status_code=400, detail="only jpg/png/gif/webp supported")
    upload_setting = await get_upload_setting(db)
    max_image_size = upload_setting["max_image_bytes"]
    max_image_mb = upload_setting["max_image_mb"]
    content = await file.read(max_image_size + 1)
    if len(content) > max_image_size:
        raise HTTPException(status_code=400, detail=f"image must be <= {max_image_mb}MB")
    stored = await save_upload_bytes(content, file.filename, file.content_type, "support")
    asset = UploadedAsset(
        source="support",
        storage=stored.storage,
        bucket=stored.bucket,
        object_key=stored.object_key,
        path=stored.path,
        url=stored.url,
        filename=Path(stored.object_key).name,
        original_name=file.filename or "",
        mime_type=file.content_type or "",
        size=len(content),
        uploader_role=role,
        user_id=current_user.id if current_user else None,
        conversation_id=conversation_id,
    )
    db.add(asset)
    await db.flush()
    public_url = f"{settings.api_prefix}/public/assets/{asset.id}/file"
    thumb_url = public_url
    thumb_content = build_image_thumbnail(content)
    if thumb_content:
        thumb_stored = await save_upload_bytes(thumb_content, "thumb.jpg", "image/jpeg", "support-thumbs")
        thumb_asset = UploadedAsset(
            source="support_thumb",
            storage=thumb_stored.storage,
            bucket=thumb_stored.bucket,
            object_key=thumb_stored.object_key,
            path=thumb_stored.path,
            url=thumb_stored.url,
            filename=Path(thumb_stored.object_key).name,
            original_name=f"thumb-{file.filename or ''}",
            mime_type="image/jpeg",
            size=len(thumb_content),
            uploader_role=role,
            user_id=current_user.id if current_user else None,
            conversation_id=conversation_id,
        )
        db.add(thumb_asset)
        await db.flush()
        thumb_url = f"{settings.api_prefix}/public/assets/{thumb_asset.id}/file"
    await db.commit()
    await db.refresh(asset)
    return {"url": public_url, "thumb_url": thumb_url, "path": public_url, "storage": stored.storage}


@router.websocket("/ws/{conversation_id}")
async def support_websocket(
    websocket: WebSocket,
    conversation_id: str,
    role: str = Query(default="user"),
    token: str = Query(default=""),
    user_id: str = Query(default=""),
):
    if role not in {"user", "admin", "merchant"}:
        await websocket.close(code=1008)
        return
    async with SessionLocal() as db:
        conversation = await db.get(SupportConversation, conversation_id)
        if not conversation:
            await websocket.close(code=1008)
            return
        if role == "admin":
            if token == settings.admin_api_key:
                pass
            else:
                session = await db.scalar(
                    select(UserAccount)
                    .join(UserSession, UserSession.user_id == UserAccount.id)
                    .where(UserSession.token == token, UserAccount.role == "admin", UserAccount.status == "active")
                )
                if not session:
                    await websocket.close(code=1008)
                    return
        elif role == "merchant":
            session = await db.get(SchoolMerchantSession, token)
            if not session or session.expires_at < datetime.now() or session.school_id != conversation.school_id:
                await websocket.close(code=1008)
                return
        elif conversation.user_id != user_id:
            await websocket.close(code=1008)
            return
    await manager.connect(conversation_id, role, websocket)
    await sync_presence(conversation_id)
    await manager.broadcast(conversation_id, {"type": "presence", "role": role, "online": True})
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type", "message")
            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if event_type == "typing":
                await manager.broadcast(conversation_id, {"type": "typing", "role": role, "typing": bool(data.get("typing"))})
                continue
            if event_type != "message":
                continue
            content = str(data.get("content", "")).strip()[:2000]
            message_type = str(data.get("message_type", "text")).strip() or "text"
            image_url = str(data.get("image_url", "")).strip()
            image_thumb_url = str(data.get("image_thumb_url", "")).strip()
            if message_type == "image" and not image_url:
                await websocket.send_json({"type": "error", "message": "鍥剧墖娑堟伅缂哄皯鍦板潃"})
                continue
            if message_type == "text" and not content:
                continue
            async with SessionLocal() as db:
                conversation = await db.get(SupportConversation, conversation_id)
                if not conversation or conversation.status != "open":
                    await websocket.send_json({"type": "error", "message": "conversation closed"})
                    continue
                if role == "user":
                    sender_name = conversation.user_name
                elif role == "merchant":
                    sender_name = conversation.school_name or "Merchant Support"
                else:
                    sender_name = "Platform Support"
                message = SupportMessage(
                    conversation_id=conversation_id,
                    sender_role=role,
                    sender_name=sender_name,
                    content=content,
                    message_type=message_type,
                    image_url=image_url,
                    image_thumb_url=image_thumb_url,
                )
                db.add(message)
                conversation.last_message = _message_preview(content, message_type)
                if role == "user":
                    conversation.unread_admin += 1
                    conversation.unread_merchant += 1
                elif role == "merchant":
                    conversation.unread_user += 1
                    conversation.unread_admin += 1
                else:
                    conversation.unread_user += 1
                    conversation.unread_merchant += 1
                await db.commit()
                await db.refresh(message)
                payload = {"type": "message", "message": message_dict(message)}
            await manager.broadcast(conversation_id, payload)
    except WebSocketDisconnect:
        manager.disconnect(conversation_id, role, websocket)
        await sync_presence(conversation_id)
        await manager.broadcast(conversation_id, {"type": "presence", "role": role, "online": False})
    except Exception:
        manager.disconnect(conversation_id, role, websocket)
        await sync_presence(conversation_id)
