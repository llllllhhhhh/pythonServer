from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UserAccount(Base, TimestampMixin):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(60), default="小徒同学")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    avatar: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[int] = mapped_column(Integer, default=0)
    exam_status: Mapped[str] = mapped_column(String(20), default="备考中")
    is_registered: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, comment="所需积分")
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
    user_online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    admin_online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_user_online_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_admin_online_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    sender_role: Mapped[str] = mapped_column(String(20), index=True)
    sender_name: Mapped[str] = mapped_column(String(60))
    content: Mapped[str] = mapped_column(Text, default="")
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    image_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class StudyProduct(Base, TimestampMixin):
    __tablename__ = "study_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    product_type: Mapped[str] = mapped_column(String(30), index=True, comment="community/package/material")
    subtitle: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    original_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    billing_cycle: Mapped[str] = mapped_column(String(30), default="once")
    cover: Mapped[str] = mapped_column(Text, default="")
    benefits: Mapped[list] = mapped_column(JSON, default=list)
    trial_minutes: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=-1)
    sales: Mapped[int] = mapped_column(Integer, default=0)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    installment_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    installment_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class StudyContent(Base, TimestampMixin):
    __tablename__ = "study_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(30), default="lesson")
    summary: Mapped[str] = mapped_column(String(255), default="")
    resource_url: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    preview: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[bool] = mapped_column(Boolean, default=True)


class StudyOrder(Base, TimestampMixin):
    __tablename__ = "study_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    product_type: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    payment_method: Mapped[str] = mapped_column(String(20), default="wechat")
    payment_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    transaction_id: Mapped[str] = mapped_column(String(80), default="")
    installment_no: Mapped[int] = mapped_column(Integer, default=1)
    installment_count: Mapped[int] = mapped_column(Integer, default=1)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserEntitlement(Base, TimestampMixin):
    __tablename__ = "user_entitlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    entitlement_type: Mapped[str] = mapped_column(String(30), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)


class LearningProfile(Base, TimestampMixin):
    __tablename__ = "learning_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    target_exam: Mapped[str] = mapped_column(String(80), default="待设置")
    current_stage: Mapped[str] = mapped_column(String(80), default="基础阶段")
    total_minutes: Mapped[int] = mapped_column(Integer, default=0)
    checkin_days: Mapped[int] = mapped_column(Integer, default=0)
    last_checkin_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    advisor_notes: Mapped[str] = mapped_column(Text, default="")
