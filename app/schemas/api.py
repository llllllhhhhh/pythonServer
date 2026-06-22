from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DecorationPayload(BaseModel):
    brand: dict[str, Any]
    pages: list[dict[str, Any]]
    routes: list[dict[str, Any]] = Field(default_factory=list)
    points: dict[str, Any] = Field(default_factory=dict)


class DecorationResponse(BaseModel):
    id: int | None = None
    version: int = 1
    status: str
    content: dict[str, Any]
    published_at: datetime | None = None


class RouteBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "户外"
    days: str = "3天2夜"
    price: Decimal = Field(default=0, description="兑换路线所需积分")
    stock: int = 0
    agency: str = ""
    image: str = ""
    description: str = ""
    status: bool = True


class RouteCreate(RouteBase):
    pass


class RouteUpdate(RouteBase):
    pass


class RouteOut(RouteBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class PointRulePayload(BaseModel):
    invite_score: int = 1
    exchange_score: int = 100
    valid_days: int = 365
    yearly_limit: int = 1
    monthly_stock: int = 50
    enabled: bool = True


class PointRuleOut(PointRulePayload):
    id: int
    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_no: str
    order_type: str
    title: str
    user_name: str
    phone: str
    travel_date: str
    agency: str
    amount_text: str
    status: int
    reject_reason: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OrderReview(BaseModel):
    status: int = Field(ge=1, le=2)
    agency: str | None = None
    reject_reason: str = ""


class InviteOut(BaseModel):
    id: int
    inviter_id: str
    invitee_phone: str
    device_id: str
    score_granted: bool
    abnormal: bool
    frozen: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PreferenceEventPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    user_name: str = Field(default="小徒同学", max_length=60)
    preference_type: str = Field(pattern="^(route|study)$")
    target_key: str = Field(min_length=1, max_length=120)
    target_name: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=30)
    score: int = Field(ge=-10, le=10)


class AnnouncementBase(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=255)
    content: str = Field(default="", min_length=1)
    tag: str = Field(default="平台公告", max_length=40)
    pinned: bool = False
    status: bool = False
    published_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class AnnouncementCreate(AnnouncementBase):
    pass


class AnnouncementUpdate(AnnouncementBase):
    pass


class AnnouncementOut(AnnouncementBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SupportSessionPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    user_name: str = Field(default="小徒同学", max_length=60)
