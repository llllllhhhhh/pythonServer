from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DecorationConfig(Base, TimestampMixin):
    __tablename__ = "decoration_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TravelRoute(Base, TimestampMixin):
    __tablename__ = "travel_routes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), default="户外")
    days: Mapped[str] = mapped_column(String(30), default="3天2夜")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, comment="兑换路线所需积分")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    agency: Mapped[str] = mapped_column(String(120), default="")
    image: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class PointRule(Base, TimestampMixin):
    __tablename__ = "point_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    invite_score: Mapped[int] = mapped_column(Integer, default=1)
    exchange_score: Mapped[int] = mapped_column(Integer, default=100)
    valid_days: Mapped[int] = mapped_column(Integer, default=365)
    yearly_limit: Mapped[int] = mapped_column(Integer, default=1)
    monthly_stock: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TravelOrder(Base, TimestampMixin):
    __tablename__ = "travel_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_type: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(160))
    user_name: Mapped[str] = mapped_column(String(60))
    phone: Mapped[str] = mapped_column(String(30))
    travel_date: Mapped[str] = mapped_column(String(30), default="")
    agency: Mapped[str] = mapped_column(String(120), default="")
    amount_text: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="")


class InviteRelation(Base, TimestampMixin):
    __tablename__ = "invite_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inviter_id: Mapped[str] = mapped_column(String(40), index=True)
    invitee_phone: Mapped[str] = mapped_column(String(30), index=True)
    device_id: Mapped[str] = mapped_column(String(100), index=True)
    score_granted: Mapped[bool] = mapped_column(Boolean, default=True)
    abnormal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)


class PreferenceEvent(Base, TimestampMixin):
    __tablename__ = "preference_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    user_name: Mapped[str] = mapped_column(String(60), default="小徒同学")
    preference_type: Mapped[str] = mapped_column(String(20), index=True)
    target_key: Mapped[str] = mapped_column(String(120), index=True)
    target_name: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(30), index=True)
    score: Mapped[int] = mapped_column(Integer, default=1)


class PlatformAnnouncement(Base, TimestampMixin):
    __tablename__ = "platform_announcements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), index=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    tag: Mapped[str] = mapped_column(String(40), default="平台公告")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupportConversation(Base, TimestampMixin):
    __tablename__ = "support_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    user_name: Mapped[str] = mapped_column(String(60), default="小徒同学")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    last_message: Mapped[str] = mapped_column(String(255), default="")
    unread_admin: Mapped[int] = mapped_column(Integer, default=0)
    unread_user: Mapped[int] = mapped_column(Integer, default=0)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    sender_role: Mapped[str] = mapped_column(String(20), index=True)
    sender_name: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
