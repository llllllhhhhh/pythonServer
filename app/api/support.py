from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.security import get_current_user, get_optional_user, require_admin
from app.models import SupportConversation, SupportMessage, UserAccount, UserSession
from app.schemas.api import SupportSessionPayload

router = APIRouter(prefix="/support", tags=["support"])

UPLOAD_ROOT = Path(settings.upload_dir) / "support"


def message_dict(message: SupportMessage) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_role": message.sender_role,
        "sender_name": message.sender_name,
        "content": message.content,
        "message_type": message.message_type,
        "image_url": message.image_url,
        "created_at": message.created_at.isoformat() if message.created_at else datetime.now().isoformat(),
    }


def conversation_dict(item: SupportConversation) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "user_name": item.user_name,
        "status": item.status,
        "last_message": item.last_message,
        "unread_admin": item.unread_admin,
        "unread_user": item.unread_user,
        "user_online": item.user_online,
        "admin_online": item.admin_online,
        "last_user_online_at": item.last_user_online_at.isoformat() if item.last_user_online_at else None,
        "last_admin_online_at": item.last_admin_online_at.isoformat() if item.last_admin_online_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


class SupportConnectionManager:
    def __init__(self):
        self.connections: dict[str, dict[str, set[WebSocket]]] = defaultdict(lambda: {"user": set(), "admin": set()})

    async def connect(self, conversation_id: str, role: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[conversation_id][role].add(websocket)

    def disconnect(self, conversation_id: str, role: str, websocket: WebSocket):
        group = self.connections.get(conversation_id)
        if not group:
            return
        group[role].discard(websocket)
        if not group["user"] and not group["admin"]:
            self.connections.pop(conversation_id, None)

    def is_online(self, conversation_id: str, role: str) -> bool:
        return bool(self.connections.get(conversation_id, {}).get(role))

    async def broadcast(self, conversation_id: str, payload: dict):
        stale: list[tuple[str, WebSocket]] = []
        group = self.connections.get(conversation_id, {})
        for role in ("user", "admin"):
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
        if conversation.user_online:
            conversation.last_user_online_at = datetime.now()
        if conversation.admin_online:
            conversation.last_admin_online_at = datetime.now()
        await db.commit()


def _message_preview(content: str, message_type: str) -> str:
    if message_type == "image":
        return "[图片]"
    return content[:255]


@router.post("/conversations")
async def create_conversation(
    payload: SupportSessionPayload,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.user_no != payload.user_id:
        raise HTTPException(status_code=403, detail="无权创建他人会话")
    item = await db.scalar(
        select(SupportConversation).where(SupportConversation.user_id == payload.user_id).order_by(SupportConversation.updated_at.desc())
    )
    if not item:
        item = SupportConversation(id=str(uuid4()), user_id=payload.user_id, user_name=payload.user_name)
        db.add(item)
    else:
        item.user_name = payload.user_name
        item.status = "open"
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
        raise HTTPException(status_code=404, detail="客服会话不存在")
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
        raise HTTPException(status_code=404, detail="客服会话不存在")
    messages = list(await db.scalars(select(SupportMessage).where(SupportMessage.conversation_id == conversation_id).order_by(SupportMessage.id)))
    item.unread_admin = 0
    await db.commit()
    return [message_dict(message) for message in messages]


@router.patch("/admin/conversations/{conversation_id}/close", dependencies=[Depends(require_admin)])
async def close_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.get(SupportConversation, conversation_id)
    if not item:
        raise HTTPException(status_code=404, detail="客服会话不存在")
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
    if role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="不支持的上传角色")
    if role == "admin":
        await require_admin(x_admin_key=request.headers.get("x-admin-key"), authorization=request.headers.get("authorization"), db=db)
    conversation = await db.get(SupportConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="客服会话不存在")
    if role == "user" and (not current_user or conversation.user_id != current_user.user_no):
        raise HTTPException(status_code=403, detail="无权上传到该会话")
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/gif/webp 图片")
    folder = UPLOAD_ROOT / datetime.now().strftime("%Y%m")
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    target = folder / filename
    target.write_bytes(await file.read())
    relative_path = f"/uploads/support/{datetime.now().strftime('%Y%m')}/{filename}"
    return {"url": str(request.base_url).rstrip("/") + relative_path, "path": relative_path}


@router.websocket("/ws/{conversation_id}")
async def support_websocket(
    websocket: WebSocket,
    conversation_id: str,
    role: str = Query(default="user"),
    token: str = Query(default=""),
    user_id: str = Query(default=""),
):
    if role not in {"user", "admin"}:
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
            if message_type == "image" and not image_url:
                await websocket.send_json({"type": "error", "message": "图片消息缺少地址"})
                continue
            if message_type == "text" and not content:
                continue
            async with SessionLocal() as db:
                conversation = await db.get(SupportConversation, conversation_id)
                if not conversation or conversation.status != "open":
                    await websocket.send_json({"type": "error", "message": "当前会话已结束"})
                    continue
                sender_name = conversation.user_name if role == "user" else "学徒行客服"
                message = SupportMessage(
                    conversation_id=conversation_id,
                    sender_role=role,
                    sender_name=sender_name,
                    content=content,
                    message_type=message_type,
                    image_url=image_url,
                )
                db.add(message)
                conversation.last_message = _message_preview(content, message_type)
                if role == "user":
                    conversation.unread_admin += 1
                else:
                    conversation.unread_user += 1
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
