import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """Base class for predictable business exceptions."""

    def __init__(self, message: str, status_code: int = 400, code: str = "BUSINESS_ERROR") -> None:
        """Create a business exception.

        Args:
            message: Human-readable error message.
            status_code: HTTP status code to return.
            code: Stable application error code.
        """
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class IdempotencyConflictError(BusinessError):
    """Raised when the same idempotency key is submitted repeatedly."""

    def __init__(self, message: str = "订单正在处理中，请勿重复提交") -> None:
        super().__init__(message=message, status_code=409, code="IDEMPOTENCY_CONFLICT")


class InsufficientStockError(BusinessError):
    """Raised when one or more products do not have enough stock."""

    def __init__(self, message: str = "商品库存不足") -> None:
        super().__init__(message=message, status_code=409, code="INSUFFICIENT_STOCK")


class RedisRequiredError(BusinessError):
    """Raised when Redis is required but unavailable."""

    def __init__(self, message: str = "订单系统需要 Redis 支持，请检查 Redis 配置") -> None:
        super().__init__(message=message, status_code=503, code="REDIS_REQUIRED")


def error_payload(error: BusinessError) -> dict[str, Any]:
    """Build a unified API error response body."""
    return {"code": error.code, "detail": error.message}


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for business errors."""

    @app.exception_handler(BusinessError)
    async def business_error_handler(_: Request, exc: BusinessError) -> JSONResponse:
        logger.warning("business_error", extra={"code": exc.code, "message": exc.message})
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc))

