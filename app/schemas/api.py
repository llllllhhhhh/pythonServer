from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, field_validator, model_validator

from app.core.asset_access import asset_api_url, sign_asset_url
from app.core.order_status import (
    TRAVEL_CONTRACT_STATUS_TEXT,
    TRAVEL_FULFILLMENT_STATUS_TEXT,
    status_label,
)


def validate_travel_date(value: str) -> str:
    """Validate a travel date in YYYY-MM-DD or YYYY/MM/DD form; empty allowed."""
    text = (value or "").strip()
    if not text:
        return text
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            datetime.strptime(text, pattern)
            return text
        except ValueError:
            continue
    raise ValueError("出行日期格式应为 YYYY-MM-DD")


class DecorationPayload(BaseModel):
    brand: dict[str, Any]
    pages: list[dict[str, Any]]
    bottomNav: list[dict[str, Any]] = Field(default_factory=list)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    points: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)


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
    display_weight: int = 0
    status: bool = True


class RouteCreate(RouteBase):
    pass


class RouteUpdate(RouteBase):
    pass


class RouteOut(RouteBase):
    id: int
    school_id: int = 0
    review_status: str = "approved"
    reject_reason: str = ""
    model_config = ConfigDict(from_attributes=True)


class RouteReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = ""


class TravelRouteReviewCreate(BaseModel):
    order_id: int = 0
    rating: int = Field(ge=1, le=5)
    content: str = Field(default="", max_length=1200)
    images: list[str] = Field(default_factory=list, max_length=6)


class TravelRouteReviewOut(BaseModel):
    id: int
    route_id: int
    order_id: int
    order_no: str = ""
    user_id: int
    user_no: str = ""
    user_name: str = ""
    rating: int
    content: str = ""
    images: list[str] = Field(default_factory=list)
    status: str = "published"
    created_at: datetime
    updated_at: datetime

    @field_validator("images", mode="before")
    @classmethod
    def _empty_images_for_null(cls, value):
        return value if isinstance(value, list) else []

    model_config = ConfigDict(from_attributes=True)


class TravelRouteReviewListOut(BaseModel):
    route_id: int
    count: int
    average_rating: float
    items: list[TravelRouteReviewOut]


class TravelRouteReviewEligibilityOut(BaseModel):
    route_id: int
    can_review: bool
    reason: str = ""
    eligible_order_id: int = 0
    eligible_order_no: str = ""
    reviewed_order_ids: list[int] = Field(default_factory=list)


class PointRulePayload(BaseModel):
    invite_score: int = Field(default=1, ge=0, le=100000)
    checkin_score: int = Field(default=1, ge=0, le=100000)
    purchase_score: int = Field(default=5, ge=0, le=100000)
    vip_purchase_score: int = Field(default=10, ge=0, le=100000)
    exchange_score: int = Field(default=100, ge=1, le=1000000)
    valid_days: int = Field(default=365, ge=1, le=36500)
    yearly_limit: int = Field(default=1, ge=0, le=1000)
    monthly_stock: int = Field(default=50, ge=0, le=100000)
    invite_enabled: bool = True
    checkin_enabled: bool = True
    purchase_enabled: bool = True
    vip_purchase_enabled: bool = True
    enabled: bool = True


class PointRuleOut(PointRulePayload):
    id: int
    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_no: str
    user_id: int = 0
    user_no: str = ""
    route_id: int = 0
    order_type: str
    title: str
    user_name: str
    phone: str
    travel_date: str
    agency: str
    amount_text: str
    cover: str | None = ""
    image: str = ""
    points_cost: int = 0
    points_refunded: bool = False
    stock_restored: bool = False
    status: int
    reject_reason: str
    contract_status: str = "unsigned"
    contract_signer_name: str = ""
    contract_signer_phone: str = ""
    contract_id_no: str = ""
    contract_template_version: int = 0
    contract_template_snapshot_id: int = 0
    contract_template_title: str = ""
    contract_template_content: str = ""
    contract_template_travel_date_days: int = 0
    contract_signature_data: str | None = ""
    contract_signature_url: str | None = ""
    contract_signed_at: datetime | None = None
    contract_reviewed_at: datetime | None = None
    contract_reject_reason: str = ""
    fulfillment_status: str = "contract_pending"
    pickup_address: str = ""
    pickup_detail: str = ""
    traveler_count: int = 1
    emergency_contact: str = ""
    emergency_phone: str = ""
    luggage_count: int = 0
    pickup_note: str = ""
    pickup_time: str = ""
    pickup_location: str = ""
    driver_name: str = ""
    driver_phone: str = ""
    vehicle_no: str = ""
    pickup_notice: str = ""
    pickup_confirmed_at: datetime | None = None
    qr_token: str = ""
    qr_issued_at: datetime | None = None
    qr_expires_at: datetime | None = None
    checked_in_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str = ""
    exception_reason: str = ""
    created_at: datetime

    @field_validator(
        "user_no",
        "order_type",
        "title",
        "user_name",
        "phone",
        "travel_date",
        "agency",
        "amount_text",
        "cover",
        "image",
        "reject_reason",
        "contract_status",
        "contract_signer_name",
        "contract_signer_phone",
        "contract_id_no",
        "contract_template_title",
        "contract_template_content",
        "contract_signature_data",
        "contract_signature_url",
        "contract_reject_reason",
        "fulfillment_status",
        "pickup_address",
        "pickup_detail",
        "emergency_contact",
        "emergency_phone",
        "pickup_note",
        "pickup_time",
        "pickup_location",
        "driver_name",
        "driver_phone",
        "vehicle_no",
        "pickup_notice",
        "qr_token",
        "cancel_reason",
        "exception_reason",
        mode="before",
    )
    @classmethod
    def _empty_string_for_null(cls, value):
        return "" if value is None else value

    @field_serializer("contract_signature_url")
    def _serialize_contract_signature_url(self, value: str | None) -> str:
        return sign_asset_url(value)

    @computed_field
    @property
    def contract_status_text(self) -> str:
        return status_label(TRAVEL_CONTRACT_STATUS_TEXT, self.contract_status, "unsigned")

    @computed_field
    @property
    def fulfillment_status_text(self) -> str:
        return status_label(TRAVEL_FULFILLMENT_STATUS_TEXT, self.fulfillment_status)

    @computed_field
    @property
    def contract_signature_image(self) -> str:
        return sign_asset_url(self.contract_signature_url) or self.contract_signature_data or ""

    model_config = ConfigDict(from_attributes=True)


class RouteExchangePayload(BaseModel):
    travel_date: str = Field(default="", max_length=30)

    @field_validator("travel_date")
    @classmethod
    def _validate_travel_date(cls, value: str) -> str:
        return validate_travel_date(value)


class ContractTemplatePayload(BaseModel):
    title: str = Field(default="旅行服务合同", min_length=1, max_length=120)
    content: str = Field(min_length=1)
    travel_date_days: int = Field(default=30, ge=1, le=36500)
    travel_date_value: int | None = Field(default=None, ge=1, le=36500)
    travel_date_unit: str | None = Field(default=None, pattern="^(day|year)$")


class ContractTemplateOut(ContractTemplatePayload):
    version: int = 0
    snapshot_id: int = 0
    contract_template_version: int = 0
    contract_template_snapshot_id: int = 0
    travel_date_options: list[str] = Field(default_factory=list)
    travel_date_start: str = ""
    travel_date_end: str = ""


class ContractSignPayload(BaseModel):
    signer_name: str = Field(min_length=1, max_length=60)
    signer_phone: str = Field(min_length=6, max_length=30)
    id_no: str = Field(min_length=1, max_length=40)
    travel_date: str = Field(min_length=1, max_length=30)
    signature_data: str = Field(min_length=60, max_length=500000)

    @field_validator("travel_date")
    @classmethod
    def _validate_sign_travel_date(cls, value: str) -> str:
        return validate_travel_date(value)


class ContractReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = Field(default="", max_length=255)


class TravelPickupInfoPayload(BaseModel):
    pickup_address: str = Field(min_length=1, max_length=255)
    pickup_detail: str = Field(default="", max_length=255)
    traveler_count: int = Field(default=1, ge=1, le=99)
    emergency_contact: str = Field(min_length=1, max_length=60)
    emergency_phone: str = Field(min_length=6, max_length=30)
    luggage_count: int = Field(default=0, ge=0, le=99)
    pickup_note: str = Field(default="", max_length=500)


class TravelPickupSchedulePayload(BaseModel):
    pickup_time: str = Field(min_length=1, max_length=60)
    pickup_location: str = Field(min_length=1, max_length=255)
    driver_name: str = Field(default="", max_length=60)
    driver_phone: str = Field(default="", max_length=30)
    vehicle_no: str = Field(default="", max_length=40)
    pickup_notice: str = Field(default="", max_length=500)


class TravelOrderExceptionPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class TravelOrderCancelPayload(BaseModel):
    reason: str = Field(min_length=2, max_length=255)


class CustomTravelRequestCreate(BaseModel):
    destination: str = Field(min_length=1, max_length=160)
    travel_time: str = Field(default="", max_length=80)
    days: str = Field(default="", max_length=40)
    budget: str = Field(default="", max_length=80)
    people_count: str = Field(default="", max_length=40)
    special_tags: list[str] = Field(default_factory=list)
    note: str = ""


class CustomTravelReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = Field(default="", max_length=255)
    plan_title: str = Field(default="", max_length=160)
    plan_summary: str = ""
    plan_price: str = Field(default="", max_length=80)
    plan_itinerary: list[str] = Field(default_factory=list)
    plan_includes: list[str] = Field(default_factory=list)
    plan_tips: str = ""


class CustomTravelRequestOut(BaseModel):
    id: int
    request_no: str
    user_id: int
    user_no: str
    user_name: str
    phone: str
    destination: str
    travel_time: str
    days: str
    budget: str
    people_count: str
    special_tags: list[str] = Field(default_factory=list)
    note: str = ""
    status: str
    reject_reason: str = ""
    plan_title: str = ""
    plan_summary: str = ""
    plan_price: str = ""
    plan_itinerary: list[str] = Field(default_factory=list)
    plan_includes: list[str] = Field(default_factory=list)
    plan_tips: str = ""
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


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
    user_name: str = Field(default="学徒行同学", max_length=60)
    preference_type: str = Field(pattern="^(route|study)$")
    target_key: str = Field(min_length=1, max_length=120)
    target_name: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=30)
    score: int = Field(ge=-10, le=10)


class AnnouncementBase(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=255)
    content: str = Field(default="", min_length=1)
    tag: str = Field(default="骞冲彴鍏憡", max_length=40)
    pinned: bool = False
    status: bool = False
    published_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_publish_window(self) -> "AnnouncementBase":
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at must not be earlier than start_at")
        return self


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
    category: str = Field(default="鍗忚瑙勫垯", max_length=40)
    cover: str = ""
    pinned: bool = False
    vip_only: bool = False
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
    vip_locked: bool = False
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
    display_weight: int = 0
    sort_order: int = 0
    description: str = ""

    @field_validator("merchant_account", mode="before")
    @classmethod
    def normalize_empty_merchant_account(cls, value):
        return value or ""


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
    user_name: str = Field(default="学徒行同学", max_length=60)


class RegisterPayload(BaseModel):
    phone: str = Field(min_length=6, max_length=30)
    password: str = Field(min_length=6, max_length=50)
    nickname: str = Field(default="学徒行同学", max_length=60)
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


class PointLedgerOut(BaseModel):
    id: int
    transaction_no: str
    direction: str
    amount: int
    balance_before: int
    balance_after: int
    biz_type: str
    biz_no: str
    remark: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
    ledger: list[PointLedgerOut] = Field(default_factory=list)


class LoginPayload(BaseModel):
    account: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6, max_length=50)
    slider_ticket: str = Field(default="", max_length=120)


class WechatMiniLoginPayload(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    phone_code: str = Field(min_length=1, max_length=128)
    invite_code: str = Field(default="", max_length=64)
    device_id: str = Field(default="", max_length=160)


class SliderCaptchaImagePayload(BaseModel):
    name: str = Field(default="", max_length=60)
    url: str = Field(min_length=1, max_length=500)
    enabled: bool = True


class SliderCaptchaSettingsPayload(BaseModel):
    enabled: bool = True
    title: str = Field(default="拖动滑块完成验证", min_length=1, max_length=80)
    description: str = Field(default="请按住滑块，将缺口拼合后再登录。", max_length=160)
    image: str = Field(default="", max_length=500)
    images: list[SliderCaptchaImagePayload] = Field(default_factory=list, max_length=20)
    shape: str = Field(default="rounded", pattern="^(rounded|circle|square|puzzle)$")
    tolerance: int = Field(default=6, ge=2, le=20)
    expires_seconds: int = Field(default=120, ge=30, le=300)


class SliderCaptchaSettingsOut(SliderCaptchaSettingsPayload):
    pass


class SliderCaptchaChallengeOut(BaseModel):
    enabled: bool
    challenge_id: str = ""
    title: str
    description: str
    image: str
    shape: str = "rounded"
    target_x: int = 0
    piece_y: int = 0
    piece_size: int = 42
    expires_seconds: int = 120


class SliderCaptchaVerifyPayload(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=80)
    x: int = Field(ge=0, le=1000)
    scope: str = Field(pattern="^(user|admin|merchant)$")


class SliderCaptchaVerifyOut(BaseModel):
    verified: bool
    ticket: str = ""


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
    vip_enabled: bool = False
    vip_active: bool = False
    vip_type: str = ""
    vip_started_at: datetime | None = None
    vip_expires_at: datetime | None = None
    vip_remark: str = ""
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
    vip_enabled: bool = False
    vip_active: bool = False
    vip_type: str = ""
    vip_started_at: datetime | None = None
    vip_expires_at: datetime | None = None
    vip_remark: str = ""
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
    certificate_no: str
    certificate_image: str
    status: str
    reject_reason: str
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("certificate_image")
    def _serialize_certificate_image(self, value: str) -> str:
        return sign_asset_url(value)

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


class VipUpdatePayload(BaseModel):
    enabled: bool = False
    vip_type: str = Field(default="", max_length=30)
    started_at: datetime | None = None
    expires_at: datetime | None = None
    remark: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_dates(self) -> "VipUpdatePayload":
        if self.expires_at and self.started_at and self.expires_at <= self.started_at:
            raise ValueError("VIP 到期时间必须晚于开始时间")
        if self.enabled and not self.vip_type.strip():
            raise ValueError("启用 VIP 时请填写 VIP 类型")
        return self


class VipPlanPayload(BaseModel):
    id: int = Field(ge=1, le=1000000)
    name: str = Field(min_length=1, max_length=40)
    vip_type: str = Field(min_length=1, max_length=30)
    price: float = Field(ge=0.01, le=99999.99)
    duration_days: int = Field(ge=1, le=3650)
    note: str = Field(default="", max_length=100)
    badge: str = Field(default="", max_length=16)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    sort_order: int = Field(default=10, ge=0, le=9999)

    @field_validator("benefits")
    @classmethod
    def validate_benefits(cls, value: list[str]) -> list[str]:
        benefits = [str(item).strip()[:100] for item in value if str(item).strip()]
        if not benefits:
            raise ValueError("请至少填写一项 VIP 权益")
        return benefits


class VipPlanSettingsPayload(BaseModel):
    plans: list[VipPlanPayload] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "VipPlanSettingsPayload":
        ids = [plan.id for plan in self.plans]
        if len(ids) != len(set(ids)):
            raise ValueError("VIP 套餐 ID 不能重复")
        return self


class WalletAdjustPayload(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    direction: str = Field(default="income", pattern="^(income|expense)$")
    remark: str = Field(default="", max_length=255)


class PointsAdjustPayload(BaseModel):
    amount: int = Field(gt=0, le=1000000)
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
    storage_url: str = ""

    @field_serializer("path", "url")
    def _serialize_asset_url(self, _value: str) -> str:
        return sign_asset_url(asset_api_url(self.id))

    model_config = ConfigDict(from_attributes=True)



