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


class UploadSettingPayload(BaseModel):
    max_image_mb: int = Field(default=8, ge=1, le=50)


class UploadSettingOut(BaseModel):
    max_image_mb: int
    max_image_bytes: int


class RouteBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "户外"
    days: str = "3天2夜"
    price: Decimal = Field(default=0, description="所需积分")
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


class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    summary: str = Field(default="", max_length=255)
    content: str = Field(default="", min_length=1)
    category: str = Field(default="协议规则", max_length=40)
    cover: str = ""
    pinned: bool = False
    status: bool = False
    sort_order: int = 0
    published_at: datetime | None = None


class ArticleCreate(ArticleBase):
    pass


class ArticleUpdate(ArticleBase):
    pass


class ArticleOut(ArticleBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SchoolSiteBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    short_name: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=60)
    district: str = Field(default="", max_length=60)
    logo: str = ""
    status: bool = True
    current: bool = False
    review_status: str = Field(default="pending", pattern="^(pending|approved|rejected)$")
    reject_reason: str = Field(default="", max_length=255)
    merchant_account: str = Field(default="", max_length=60)
    sort_order: int = 0
    description: str = ""


class SchoolSiteCreate(SchoolSiteBase):
    merchant_password: str = Field(default="", max_length=50)


class SchoolApplicationPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    short_name: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=60)
    district: str = Field(default="", max_length=60)
    logo: str = ""
    contact_name: str = Field(default="", max_length=60)
    merchant_account: str = Field(min_length=3, max_length=60)
    merchant_password: str = Field(min_length=6, max_length=50)
    description: str = ""


class SchoolApplicationResponse(BaseModel):
    message: str
    status: str


class SchoolReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = Field(default="", max_length=255)


class MerchantPasswordPayload(BaseModel):
    merchant_account: str = Field(min_length=3, max_length=60)
    merchant_password: str = Field(min_length=6, max_length=50)


class SchoolSiteUpdate(SchoolSiteBase):
    pass


class SchoolSiteOut(SchoolSiteBase):
    id: int
    has_merchant_password: bool = False
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MerchantLoginResponse(BaseModel):
    token: str
    school: SchoolSiteOut


class SupportSessionPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    user_name: str = Field(default="小徒同学", max_length=60)


class RegisterPayload(BaseModel):
    phone: str = Field(min_length=6, max_length=30)
    password: str = Field(min_length=6, max_length=50)
    nickname: str = Field(default="小徒同学", max_length=60)
    invite_code: str = Field(default="", max_length=64)
    device_id: str = Field(default="", max_length=100)


class RegisterSubmitResponse(BaseModel):
    message: str
    status: str
    invitation_bound: bool = False


class InviteRecordOut(BaseModel):
    nickname: str
    phone: str
    status: str
    score: int
    score_granted: bool
    created_at: datetime


class InviteDashboardOut(BaseModel):
    invite_code: str
    invite_payload: str
    points: int
    invite_score: int
    exchange_score: int
    enabled: bool
    invited_count: int
    granted_count: int
    records: list[InviteRecordOut]


class LoginPayload(BaseModel):
    account: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6, max_length=50)


class UserOut(BaseModel):
    id: int
    user_no: str
    phone: str
    nickname: str
    role: str
    status: str
    avatar: str = ""
    points: int = 0
    balance: Decimal = Decimal("0.00")
    exam_status: str = "学员"
    is_registered: bool = True
    last_login_at: datetime | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class UserSummaryOut(BaseModel):
    total_users: int
    registered_users: int
    admin_users: int
    active_users: int
    pending_users: int = 0


class AdminUserRow(BaseModel):
    id: int
    user_no: str
    phone: str
    nickname: str
    role: str
    status: str
    is_registered: bool
    points: int
    balance: Decimal = Decimal("0.00")
    exam_status: str
    created_at: datetime
    last_login_at: datetime | None = None
    conversation_count: int = 0
    open_conversation: bool = False
    graduation_status: str = "not_submitted"


class GraduationCertificationOut(BaseModel):
    id: int
    user_id: int
    real_name: str
    school_name: str
    major_name: str
    graduation_date: str
    certificate_no: str
    certificate_image: str
    status: str
    reject_reason: str
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class GraduationReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = Field(default="", max_length=255)


class AdminUserDetail(BaseModel):
    user: AdminUserRow
    graduation_certification: GraduationCertificationOut | None = None


class RegistrationReviewPayload(BaseModel):
    approved: bool


class PasswordResetPayload(BaseModel):
    password: str = Field(min_length=6, max_length=50)


class WalletAdjustPayload(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    direction: str = Field(default="income", pattern="^(income|expense)$")
    remark: str = Field(default="", max_length=255)


class StatusUpdatePayload(BaseModel):
    status: str = Field(pattern="^(active|disabled|cancelled|rejected)$")


class RegistrationRow(BaseModel):
    id: int
    user_no: str
    phone: str
    nickname: str
    status: str
    exam_status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AdminUsersResponse(BaseModel):
    summary: UserSummaryOut
    users: list[AdminUserRow]


class UploadedAssetOut(BaseModel):
    id: int
    source: str
    storage: str
    bucket: str
    object_key: str
    path: str
    url: str
    filename: str
    original_name: str
    mime_type: str
    size: int
    uploader_role: str
    user_id: int | None = None
    conversation_id: str = ""
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
