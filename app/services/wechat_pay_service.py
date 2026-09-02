from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from decimal import Decimal
from pathlib import Path

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
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    canonical_url = "/v3/pay/transactions/jsapi"
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": build_authorization("POST", canonical_url, body, timestamp, nonce),
        "User-Agent": "xuetuxing/1.1",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{WECHAT_PAY_BASE_URL}{canonical_url}", content=body.encode("utf-8"), headers=headers)
    except httpx.HTTPError as exc:
        raise BusinessError("微信支付服务连接失败", status_code=502, code="WECHAT_PAY_UNAVAILABLE") from exc
    verify_notification_signature(response.headers, response.content)
    if response.status_code >= 400:
        try:
            message = response.json().get("message") or "微信支付下单失败"
        except ValueError:
            message = "微信支付下单失败"
        raise BusinessError(message, status_code=502, code="WECHAT_PAY_CREATE_FAILED")
    try:
        prepay_id = response.json()["prepay_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessError("微信支付返回数据异常", status_code=502, code="WECHAT_PAY_INVALID_RESPONSE") from exc
    return build_mini_program_payment_params(prepay_id)


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
