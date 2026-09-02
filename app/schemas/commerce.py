from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.core.order_status import COMMERCE_PAYMENT_STATUS_TEXT, status_label


class StudyContentPayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content_type: str = Field(default="lesson", pattern="^(lesson|material|test|live|service)$")
    summary: str = Field(default="", max_length=255)
    resource_url: str = ""
    duration_minutes: int = Field(default=0, ge=0)
    preview: bool = False
    sort_order: int = 0
    status: bool = True


class StudyProductPayload(BaseModel):
    school_id: int = 0
    name: str = Field(min_length=1, max_length=160)
    product_type: str = Field(pattern="^(community|package|material)$")
    subtitle: str = Field(default="", max_length=255)
    description: str = ""
    price: Decimal = Field(default=0, ge=0, max_digits=10, decimal_places=2)
    original_price: Decimal = Field(default=0, ge=0, max_digits=10, decimal_places=2)
    billing_cycle: str = Field(default="once", pattern="^(once|month|year)$")
    cover: str = ""
    benefits: list[str] = Field(default_factory=list)
    trial_minutes: int = Field(default=0, ge=0)
    stock: int = Field(default=-1, ge=-1)
    featured: bool = False
    installment_enabled: bool = False
    installment_count: int = Field(default=1, ge=1, le=24)
    status: bool = True
    review_status: str = Field(default="pending", pattern="^(pending|approved|rejected)$")
    reject_reason: str = Field(default="", max_length=255)
    contents: list[StudyContentPayload] = Field(default_factory=list)


class StudyProductReviewPayload(BaseModel):
    approved: bool
    reject_reason: str = Field(default="", max_length=255)


class StudyOrderCreate(BaseModel):
    product_id: int
    payment_method: str = Field(default="wechat", pattern="^(wechat|mock)$")
    installment_count: int = Field(default=1, ge=1, le=24)


class StudyOrderOut(BaseModel):
    id: int
    order_no: str
    user_id: int
    school_id: int = 0
    product_id: int
    product_name: str
    product_type: str
    amount: Decimal
    payment_method: str
    payment_status: str
    transaction_id: str
    installment_no: int
    installment_count: int
    paid_at: datetime | None
    created_at: datetime

    @computed_field
    @property
    def payment_status_text(self) -> str:
        return status_label(COMMERCE_PAYMENT_STATUS_TEXT, self.payment_status)

    model_config = ConfigDict(from_attributes=True)


class LearningProfileUpdate(BaseModel):
    target_exam: str = Field(default="待设置", max_length=80)
    current_stage: str = Field(default="基础阶段", max_length=80)


class LearningOnboardingUpdate(BaseModel):
    version: int = Field(ge=1, le=100000)
    answers: dict[str, str | list[str]] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, value: dict[str, str | list[str]]):
        if len(value) > 20:
            raise ValueError("Too many onboarding answers")
        normalized: dict[str, str | list[str]] = {}
        for raw_key, raw_answer in value.items():
            key = str(raw_key).strip()
            if not key or len(key) > 80:
                raise ValueError("Invalid onboarding answer key")
            if isinstance(raw_answer, list):
                if len(raw_answer) > 20:
                    raise ValueError("Too many values for an onboarding answer")
                normalized[key] = [str(item).strip()[:160] for item in raw_answer if str(item).strip()]
            else:
                normalized[key] = str(raw_answer).strip()[:160]
        return normalized
