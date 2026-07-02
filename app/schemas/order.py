from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreateItem(BaseModel):
    """Single product item in an order creation request."""

    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=99)
    installment_count: int = Field(default=1, ge=1, le=24)


class OrderCreateRequest(BaseModel):
    """Request model for creating a commerce order."""

    items: list[OrderCreateItem] = Field(min_length=1, max_length=20)
    payment_method: str = Field(default="balance", pattern="^(wechat|balance)$")
    idempotency_key: str = Field(min_length=8, max_length=120)


class OrderItemOut(BaseModel):
    """Order item response model."""

    id: int
    product_id: int
    product_name: str
    product_type: str
    school_id: int
    unit_price: Decimal
    quantity: int
    total_amount: Decimal
    installment_count: int = 1
    stock_deducted: bool
    status: str

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    """Commerce order response model."""

    id: int
    order_no: str
    user_id: int
    school_id: int
    total_amount: Decimal
    payable_amount: Decimal
    payment_method: str
    payment_status: str
    status: str
    transaction_id: str
    paid_at: datetime | None
    canceled_at: datetime | None
    cancel_reason: str
    created_at: datetime
    items: list[OrderItemOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
