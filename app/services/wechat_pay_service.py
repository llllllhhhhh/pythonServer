from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models import CommerceOrder


WECHAT_PAY_BASE_URL = "https://api.mch.weixin.qq.com"


def is_wechat_pay_configured() -> bool:
    common = all(
        (
            settings.wechat_mini_app_id,
            settings.wechat_mch_id,
            settings.wechat_api_v3_key,
            settings.wechat_notify_url,
            settings.wechat_merchant_serial_no,
            settings.wechat_merchant_private_key_path,
        )
    )
    return common and bool(settings.wechat_pay_public_key_id and settings.wechat_pay_public_key_path)


def ensure_wechat_pay_configured() -> None:
    if not is_wechat_pay_configured():
        raise BusinessError(
            "微信支付商户参数未配置完整",
            status_code=503,
            code="WECHAT_PAY_NOT_CONFIGURED",
        )


def yuan_to_fen(amount: Decimal) -> int:
    normalized = Decimal(amount)
    cents = normalized * 100
    if cents != cents.to_integral_value() or cents < 1:
        raise BusinessError(
            "微信支付金额必须是大于等于 0.01 元的整数分",
            status_code=400,
            code="INVALID_WECHAT_PAY_AMOUNT",
        )
    return int(cents)


def _read_required_file(path_value: str, label: str) -> bytes:
    path = Path(path_value).expanduser()
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BusinessError(
            f"无法读取{label}",
            status_code=503,
            code="WECHAT_PAY_CERTIFICATE_ERROR",
        ) from exc


def _merchant_private_key():
    raw = _read_required_file(settings.wechat_merchant_private_key_path, "微信支付商户私钥")
    try:
        return serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError) as exc:
        raise BusinessError(
            "微信支付商户私钥格式错误",
            status_code=503,
            code="WECHAT_PAY_CERTIFICATE_ERROR",
        ) from exc


def _notification_public_key(serial: str):
    if serial != settings.wechat_pay_public_key_id:
        raise BusinessError("微信支付公钥 ID 不匹配", status_code=400, code="WECHAT_PAY_INVALID_SIGNATURE")
    raw = _read_required_file(settings.wechat_pay_public_key_path, "微信支付公钥")
    try:
        return serialization.load_pem_public_key(raw)
    except ValueError as exc:
        raise BusinessError(
            "微信支付公钥格式错误",
            status_code=503,
            code="WECHAT_PAY_CERTIFICATE_ERROR",
        ) from exc


def _rsa_sign(message: str) -> str:
    signature = _merchant_private_key().sign(
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def build_authorization(method: str, canonical_url: str, body: str, timestamp: str, nonce: str) -> str:
    message = f"{method.upper()}\n{canonical_url}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _rsa_sign(message)
    return (
        'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{settings.wechat_mch_id}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{settings.wechat_merchant_serial_no}",'
        f'signature="{signature}"'
    )


def build_mini_program_payment_params(prepay_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    package = f"prepay_id={prepay_id}"
    pay_sign = _rsa_sign(f"{settings.wechat_mini_app_id}\n{timestamp}\n{nonce}\n{package}\n")
    return {
        "timeStamp": timestamp,
        "nonceStr": nonce,
        "package": package,
        "signType": "RSA",
        "paySign": pay_sign,
    }


async def _request_json(
    method: str,
    canonical_url: str,
    payload: dict | None = None,
    *,
    expected_statuses: tuple[int, ...] = (200,),
) -> dict:
    ensure_wechat_pay_configured()
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else ""
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": build_authorization(method, canonical_url, body, timestamp, nonce),
        "User-Agent": "xuetuxing/1.1",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                method.upper(),
                f"{WECHAT_PAY_BASE_URL}{canonical_url}",
                content=body.encode("utf-8") if body else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise BusinessError("微信支付服务连接失败", status_code=502, code="WECHAT_PAY_UNAVAILABLE") from exc
    verify_notification_signature(response.headers, response.content)
    if response.status_code not in expected_statuses:
        try:
            error = response.json()
        except ValueError:
            error = {}
        provider_code = str(error.get("code") or "REQUEST_FAILED")
        message = str(error.get("message") or "微信支付请求失败")
        raise BusinessError(message, status_code=502, code=f"WECHAT_PAY_{provider_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise BusinessError("微信支付返回数据异常", status_code=502, code="WECHAT_PAY_INVALID_RESPONSE") from exc


async def create_jsapi_payment(order: CommerceOrder, description: str, openid: str) -> dict[str, str]:
    ensure_wechat_pay_configured()
    if not openid:
        raise BusinessError(
            "当前账号未绑定微信身份，请使用微信一键登录后支付",
            status_code=400,
            code="WECHAT_OPENID_REQUIRED",
        )
    payload = {
        "appid": settings.wechat_mini_app_id,
        "mchid": settings.wechat_mch_id,
        "description": description.strip()[:127] or "学徒行学习服务",
        "out_trade_no": order.order_no,
        "notify_url": settings.wechat_notify_url,
        "amount": {"total": yuan_to_fen(order.payable_amount), "currency": "CNY"},
        "payer": {"openid": openid},
    }
    canonical_url = "/v3/pay/transactions/jsapi"
    result = await _request_json("POST", canonical_url, payload, expected_statuses=(200,))
    try:
        prepay_id = result["prepay_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessError("微信支付返回数据异常", status_code=502, code="WECHAT_PAY_INVALID_RESPONSE") from exc
    return build_mini_program_payment_params(prepay_id)


async def query_transaction(order_no: str) -> dict:
    canonical_url = (
        f"/v3/pay/transactions/out-trade-no/{quote(order_no, safe='')}"
        f"?mchid={quote(settings.wechat_mch_id, safe='')}"
    )
    return await _request_json("GET", canonical_url)


async def close_transaction(order_no: str) -> None:
    canonical_url = f"/v3/pay/transactions/out-trade-no/{quote(order_no, safe='')}/close"
    await _request_json(
        "POST",
        canonical_url,
        {"mchid": settings.wechat_mch_id},
        expected_statuses=(204,),
    )


async def create_refund(order: CommerceOrder, refund_no: str, reason: str = "平台退款") -> dict:
    notify_url = settings.resolved_wechat_refund_notify_url
    if not notify_url:
        raise BusinessError("微信退款回调地址未配置", status_code=503, code="WECHAT_REFUND_NOT_CONFIGURED")
    payload = {
        "out_trade_no": order.order_no,
        "out_refund_no": refund_no,
        "reason": reason.strip()[:80] or "平台退款",
        "notify_url": notify_url,
        "amount": {
            "refund": yuan_to_fen(order.payable_amount),
            "total": yuan_to_fen(order.payable_amount),
            "currency": "CNY",
        },
    }
    return await _request_json("POST", "/v3/refund/domestic/refunds", payload)


async def query_refund(refund_no: str) -> dict:
    canonical_url = f"/v3/refund/domestic/refunds/{quote(refund_no, safe='')}"
    return await _request_json("GET", canonical_url)


def validate_successful_transaction(order: CommerceOrder, transaction: dict) -> str:
    if transaction.get("trade_state") != "SUCCESS":
        raise BusinessError("微信订单尚未支付成功", status_code=409, code="WECHAT_PAY_NOT_SUCCESS")
    if transaction.get("mchid") != settings.wechat_mch_id:
        raise BusinessError("微信支付商户号不匹配", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    if transaction.get("appid") != settings.wechat_mini_app_id:
        raise BusinessError("微信支付 AppID 不匹配", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    if str(transaction.get("out_trade_no") or "") != order.order_no:
        raise BusinessError("微信支付订单号不匹配", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    amount = transaction.get("amount") or {}
    try:
        paid_total = int(amount.get("total", -1))
    except (TypeError, ValueError) as exc:
        raise BusinessError("微信支付金额格式错误", status_code=400, code="WECHAT_PAY_AMOUNT_MISMATCH") from exc
    if amount.get("currency", "CNY") != "CNY" or paid_total != yuan_to_fen(order.payable_amount):
        raise BusinessError("微信支付金额不匹配", status_code=400, code="WECHAT_PAY_AMOUNT_MISMATCH")
    transaction_id = str(transaction.get("transaction_id") or "")
    if not transaction_id:
        raise BusinessError("微信支付交易号缺失", status_code=400, code="WECHAT_PAY_ORDER_MISMATCH")
    return transaction_id


def validate_refund_result(order: CommerceOrder, refund: dict, refund_no: str) -> tuple[str, str]:
    if str(refund.get("out_trade_no") or order.order_no) != order.order_no:
        raise BusinessError("微信退款订单号不匹配", status_code=400, code="WECHAT_REFUND_ORDER_MISMATCH")
    if str(refund.get("out_refund_no") or "") != refund_no:
        raise BusinessError("微信退款单号不匹配", status_code=400, code="WECHAT_REFUND_ORDER_MISMATCH")
    amount = refund.get("amount") or {}
    try:
        refund_total = int(amount.get("refund", -1))
    except (TypeError, ValueError) as exc:
        raise BusinessError("微信退款金额格式错误", status_code=400, code="WECHAT_REFUND_AMOUNT_MISMATCH") from exc
    if refund_total != yuan_to_fen(order.payable_amount):
        raise BusinessError("微信退款金额不匹配", status_code=400, code="WECHAT_REFUND_AMOUNT_MISMATCH")
    status = str(refund.get("refund_status") or refund.get("status") or "PROCESSING").upper()
    return status, str(refund.get("refund_id") or "")


def verify_notification_signature(headers, body: bytes, now: int | None = None) -> None:
    ensure_wechat_pay_configured()
    timestamp = headers.get("Wechatpay-Timestamp") or headers.get("wechatpay-timestamp")
    nonce = headers.get("Wechatpay-Nonce") or headers.get("wechatpay-nonce")
    signature = headers.get("Wechatpay-Signature") or headers.get("wechatpay-signature")
    serial = headers.get("Wechatpay-Serial") or headers.get("wechatpay-serial")
    if not all((timestamp, nonce, signature, serial)):
        raise BusinessError("微信支付回调签名头不完整", status_code=400, code="WECHAT_PAY_INVALID_SIGNATURE")
    try:
        callback_time = int(timestamp)
    except ValueError as exc:
        raise BusinessError("微信支付回调时间戳错误", status_code=400, code="WECHAT_PAY_INVALID_SIGNATURE") from exc
    if abs((now or int(time.time())) - callback_time) > 300:
        raise BusinessError("微信支付回调已过期", status_code=400, code="WECHAT_PAY_INVALID_SIGNATURE")
    message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n".encode("utf-8")
    try:
        _notification_public_key(serial).verify(
            base64.b64decode(signature, validate=True),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise BusinessError("微信支付回调签名无效", status_code=400, code="WECHAT_PAY_INVALID_SIGNATURE") from exc


def decrypt_notification(body: bytes) -> dict:
    try:
        envelope = json.loads(body)
        resource = envelope["resource"]
        ciphertext = base64.b64decode(resource["ciphertext"], validate=True)
        plaintext = AESGCM(settings.wechat_api_v3_key.encode("utf-8")).decrypt(
            resource["nonce"].encode("utf-8"),
            ciphertext,
            (resource.get("associated_data") or "").encode("utf-8"),
        )
        return json.loads(plaintext)
    except (KeyError, TypeError, ValueError, InvalidTag, binascii.Error) as exc:
        raise BusinessError("微信支付回调解密失败", status_code=400, code="WECHAT_PAY_DECRYPT_FAILED") from exc
