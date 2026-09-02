from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.services.wechat_pay_service import (
    build_mini_program_payment_params,
    close_transaction,
    create_refund,
    create_jsapi_payment,
    decrypt_notification,
    query_transaction,
    validate_successful_transaction,
    validate_refund_result,
    verify_notification_signature,
    yuan_to_fen,
)


class FakeWechatOrder:
    order_no = "XO-WECHAT-TEST-001"
    payable_amount = Decimal("0.01")


class FakeWechatClient:
    last_request: dict | None = None
    response_status = 200
    response_json: dict | None = {"prepay_id": "wx-prepay-test"}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, content, headers):
        self.__class__.last_request = {"method": method, "url": url, "content": content, "headers": headers}
        request = httpx.Request(method, url)
        if self.__class__.response_json is None:
            return httpx.Response(self.__class__.response_status, content=b"", request=request)
        return httpx.Response(self.__class__.response_status, json=self.__class__.response_json, request=request)


class WechatPayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.wechat_pay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.merchant_key_path = root / "merchant.pem"
        self.wechat_pay_public_key_path = root / "wechatpay_pub.pem"
        self.merchant_key_path.write_bytes(
            self.merchant_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.wechat_pay_public_key_path.write_bytes(
            self.wechat_pay_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        self.original = {
            "wechat_mini_app_id": settings.wechat_mini_app_id,
            "wechat_mch_id": settings.wechat_mch_id,
            "wechat_api_v3_key": settings.wechat_api_v3_key,
            "wechat_notify_url": settings.wechat_notify_url,
            "wechat_merchant_serial_no": settings.wechat_merchant_serial_no,
            "wechat_merchant_private_key_path": settings.wechat_merchant_private_key_path,
            "wechat_pay_public_key_id": settings.wechat_pay_public_key_id,
            "wechat_pay_public_key_path": settings.wechat_pay_public_key_path,
        }
        settings.wechat_mini_app_id = "wx-test-app"
        settings.wechat_mch_id = "1900000109"
        settings.wechat_api_v3_key = "0123456789abcdef0123456789abcdef"
        settings.wechat_notify_url = "https://example.com/api/v1/commerce/wechat/notify"
        settings.wechat_merchant_serial_no = "MERCHANT-SERIAL"
        settings.wechat_merchant_private_key_path = str(self.merchant_key_path)
        settings.wechat_pay_public_key_id = "PUB_KEY_ID_TEST"
        settings.wechat_pay_public_key_path = str(self.wechat_pay_public_key_path)
        FakeWechatClient.response_status = 200
        FakeWechatClient.response_json = {"prepay_id": "wx-prepay-test"}

    def tearDown(self) -> None:
        for key, value in self.original.items():
            setattr(settings, key, value)
        self.temp_dir.cleanup()

    def test_amount_requires_at_least_one_exact_cent(self) -> None:
        self.assertEqual(1, yuan_to_fen(Decimal("0.01")))
        with self.assertRaises(BusinessError):
            yuan_to_fen(Decimal("0.001"))

    def test_successful_transaction_requires_matching_identity_and_amount(self) -> None:
        transaction = {
            "trade_state": "SUCCESS",
            "appid": settings.wechat_mini_app_id,
            "mchid": settings.wechat_mch_id,
            "out_trade_no": FakeWechatOrder.order_no,
            "transaction_id": "4200000000001",
            "amount": {"total": 1, "currency": "CNY"},
        }
        self.assertEqual("4200000000001", validate_successful_transaction(FakeWechatOrder(), transaction))
        transaction["amount"]["total"] = 2
        with self.assertRaises(BusinessError):
            validate_successful_transaction(FakeWechatOrder(), transaction)

    def test_refund_result_requires_matching_number_and_amount(self) -> None:
        refund = {
            "out_trade_no": FakeWechatOrder.order_no,
            "out_refund_no": "RF-TEST-001",
            "refund_id": "5030000000001",
            "status": "PROCESSING",
            "amount": {"refund": 1, "total": 1, "currency": "CNY"},
        }
        self.assertEqual(
            ("PROCESSING", "5030000000001"),
            validate_refund_result(FakeWechatOrder(), refund, "RF-TEST-001"),
        )
        refund["amount"]["refund"] = 2
        with self.assertRaises(BusinessError):
            validate_refund_result(FakeWechatOrder(), refund, "RF-TEST-001")

    async def test_jsapi_order_and_client_signature(self) -> None:
        with patch("app.services.wechat_pay_service.httpx.AsyncClient", FakeWechatClient), patch(
            "app.services.wechat_pay_service.verify_notification_signature"
        ):
            params = await create_jsapi_payment(FakeWechatOrder(), "微信支付测试商品", "openid-test")
        self.assertEqual("prepay_id=wx-prepay-test", params["package"])
        request = FakeWechatClient.last_request
        self.assertIn("WECHATPAY2-SHA256-RSA2048", request["headers"]["Authorization"])
        signed = (
            f"{settings.wechat_mini_app_id}\n{params['timeStamp']}\n"
            f"{params['nonceStr']}\n{params['package']}\n"
        ).encode()
        self.merchant_key.public_key().verify(
            base64.b64decode(params["paySign"]), signed, padding.PKCS1v15(), hashes.SHA256()
        )

    async def test_query_close_and_refund_requests(self) -> None:
        with patch("app.services.wechat_pay_service.httpx.AsyncClient", FakeWechatClient), patch(
            "app.services.wechat_pay_service.verify_notification_signature"
        ):
            FakeWechatClient.response_json = {
                "out_trade_no": FakeWechatOrder.order_no,
                "trade_state": "SUCCESS",
            }
            result = await query_transaction(FakeWechatOrder.order_no)
            self.assertEqual("SUCCESS", result["trade_state"])
            self.assertEqual("GET", FakeWechatClient.last_request["method"])
            self.assertIn("/v3/pay/transactions/out-trade-no/", FakeWechatClient.last_request["url"])

            FakeWechatClient.response_status = 204
            FakeWechatClient.response_json = None
            await close_transaction(FakeWechatOrder.order_no)
            self.assertEqual("POST", FakeWechatClient.last_request["method"])
            self.assertTrue(FakeWechatClient.last_request["url"].endswith("/close"))

            FakeWechatClient.response_status = 200
            FakeWechatClient.response_json = {
                "refund_id": "5030000000001",
                "out_refund_no": "RF-TEST-001",
                "status": "PROCESSING",
            }
            refund = await create_refund(FakeWechatOrder(), "RF-TEST-001")
            self.assertEqual("PROCESSING", refund["status"])
            payload = json.loads(FakeWechatClient.last_request["content"])
            self.assertEqual({"refund": 1, "total": 1, "currency": "CNY"}, payload["amount"])
            self.assertTrue(payload["notify_url"].endswith("/refund-notify"))

    def test_notification_signature_and_decryption(self) -> None:
        transaction = {
            "appid": settings.wechat_mini_app_id,
            "mchid": settings.wechat_mch_id,
            "out_trade_no": FakeWechatOrder.order_no,
            "transaction_id": "4200000000001",
            "trade_state": "SUCCESS",
            "amount": {"total": 1, "currency": "CNY"},
        }
        nonce = "0123456789ab"
        associated_data = "transaction"
        ciphertext = AESGCM(settings.wechat_api_v3_key.encode()).encrypt(
            nonce.encode(), json.dumps(transaction).encode(), associated_data.encode()
        )
        envelope = {
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": nonce,
                "associated_data": associated_data,
            },
        }
        body = json.dumps(envelope, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        callback_nonce = "callback-nonce"
        message = f"{timestamp}\n{callback_nonce}\n{body.decode()}\n".encode()
        signature = self.wechat_pay_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        headers = {
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": callback_nonce,
            "Wechatpay-Signature": base64.b64encode(signature).decode(),
            "Wechatpay-Serial": settings.wechat_pay_public_key_id,
        }
        verify_notification_signature(headers, body)
        self.assertEqual(transaction, decrypt_notification(body))
        with self.assertRaises(BusinessError):
            verify_notification_signature(headers, body + b" ")

    def test_notification_rejects_unknown_public_key_id(self) -> None:
        body = b'{"id":"notification-test"}'
        timestamp = str(int(time.time()))
        nonce = "public-key-nonce"
        message = f"{timestamp}\n{nonce}\n{body.decode()}\n".encode()
        signature = self.wechat_pay_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        with self.assertRaises(BusinessError):
            verify_notification_signature(
                {
                    "Wechatpay-Timestamp": timestamp,
                    "Wechatpay-Nonce": nonce,
                    "Wechatpay-Signature": base64.b64encode(signature).decode(),
                    "Wechatpay-Serial": "PUB_KEY_ID_UNKNOWN",
                },
                body,
            )

    def test_build_payment_params_requires_configured_key(self) -> None:
        params = build_mini_program_payment_params("prepay")
        self.assertEqual("RSA", params["signType"])
