from enum import IntEnum, StrEnum


class CommercePaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class CommerceOrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"


class TravelOrderStatus(IntEnum):
    CREATED = 0
    COMPLETED = 1
    CANCELLED = 2


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TravelContractStatus(StrEnum):
    UNSIGNED = "unsigned"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TravelFulfillmentStatus(StrEnum):
    CONTRACT_PENDING = "contract_pending"
    CONTRACT_REVIEWING = "contract_reviewing"
    CONTRACT_REJECTED = "contract_rejected"
    INFO_PENDING = "info_pending"
    INFO_SUBMITTED = "info_submitted"
    PICKUP_CONFIRMED = "pickup_confirmed"
    USER_CONFIRMED = "user_confirmed"
    QR_ISSUED = "qr_issued"
    CHECKED_IN = "checked_in"
    IN_TRIP = "in_trip"
    COMPLETED = "completed"
    EXCEPTION = "exception"
    CANCELLED = "cancelled"


COMMERCE_PAYMENT_STATUS_TEXT = {
    CommercePaymentStatus.PENDING: "待支付",
    CommercePaymentStatus.PAID: "已支付",
    CommercePaymentStatus.CANCELED: "已取消",
    CommercePaymentStatus.REFUNDED: "已退款",
    "cancelled": "已取消",
}

COMMERCE_ORDER_STATUS_TEXT = {
    CommerceOrderStatus.PENDING: "待支付",
    CommerceOrderStatus.PAID: "已支付",
    CommerceOrderStatus.CANCELED: "已取消",
    "cancelled": "已取消",
}

REVIEW_STATUS_TEXT = {
    ReviewStatus.PENDING: "待审核",
    ReviewStatus.APPROVED: "已通过",
    ReviewStatus.REJECTED: "已驳回",
}

TRAVEL_CONTRACT_STATUS_TEXT = {
    TravelContractStatus.UNSIGNED: "未签署",
    TravelContractStatus.PENDING: "待审核",
    TravelContractStatus.APPROVED: "已通过",
    TravelContractStatus.REJECTED: "已驳回",
}

TRAVEL_FULFILLMENT_STATUS_TEXT = {
    TravelFulfillmentStatus.CONTRACT_PENDING: "待签合同",
    TravelFulfillmentStatus.CONTRACT_REVIEWING: "合同审核中",
    TravelFulfillmentStatus.CONTRACT_REJECTED: "合同驳回",
    TravelFulfillmentStatus.INFO_PENDING: "待填写接送信息",
    TravelFulfillmentStatus.INFO_SUBMITTED: "待安排接送",
    TravelFulfillmentStatus.PICKUP_CONFIRMED: "待用户确认接送",
    TravelFulfillmentStatus.USER_CONFIRMED: "待核销",
    TravelFulfillmentStatus.QR_ISSUED: "已生成核销码",
    TravelFulfillmentStatus.CHECKED_IN: "已核销",
    TravelFulfillmentStatus.IN_TRIP: "行程中",
    TravelFulfillmentStatus.COMPLETED: "已完成",
    TravelFulfillmentStatus.EXCEPTION: "异常",
    TravelFulfillmentStatus.CANCELLED: "已取消",
}


def status_label(mapping: dict, value: str | StrEnum | None, default: str = "待处理") -> str:
    return mapping.get(value) or mapping.get(str(value or "")) or default
