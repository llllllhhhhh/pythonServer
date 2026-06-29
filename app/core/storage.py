from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.config import settings


@dataclass
class StoredFile:
    url: str
    path: str
    storage: str
    bucket: str
    object_key: str


@dataclass
class LoadedFile:
    content: bytes
    content_type: str


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix else ".jpg"


def _content_type(content_type: str | None, suffix: str) -> str:
    if content_type:
        return content_type
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _obs_endpoint() -> str:
    return (settings.obs_endpoint or f"obs.{settings.obs_region}.myhuaweicloud.com").replace("https://", "").replace("http://", "").rstrip("/")


def _missing_obs_config() -> list[str]:
    required = {
        "OBS_ACCESS_KEY": settings.obs_access_key,
        "OBS_SECRET_KEY": settings.obs_secret_key,
        "OBS_BUCKET": settings.obs_bucket,
        "OBS_REGION": settings.obs_region,
    }
    return [key for key, value in required.items() if not str(value or "").strip()]


def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes:
    key_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region.encode("utf-8"), hashlib.sha256).digest()
    key_service = hmac.new(key_region, b"s3", hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


async def _put_obs_object(content: bytes, object_key: str, content_type: str) -> StoredFile:
    bucket = settings.obs_bucket
    endpoint = _obs_endpoint()
    region = settings.obs_region
    host = f"{bucket}.{endpoint}"
    url = f"https://{host}/{quote(object_key, safe='/~')}"

    now = datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(content).hexdigest()
    canonical_uri = "/" + quote(object_key, safe="/~")
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        "x-amz-acl:public-read\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-acl;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(settings.obs_secret_key, date_stamp, region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": content_type,
        "Host": host,
        "x-amz-acl": "public-read",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            "AWS4-HMAC-SHA256 "
            f"Credential={settings.obs_access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.put(url, content=content, headers=headers)
    response.raise_for_status()
    public_base = settings.obs_public_base_url.rstrip("/")
    public_url = f"{public_base}/{object_key}" if public_base else url
    return StoredFile(url=public_url, path=public_url, storage="obs", bucket=bucket, object_key=object_key)


async def _get_obs_object(object_key: str) -> LoadedFile:
    bucket = settings.obs_bucket
    endpoint = _obs_endpoint()
    region = settings.obs_region
    host = f"{bucket}.{endpoint}"
    url = f"https://{host}/{quote(object_key, safe='/~')}"

    now = datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_uri = "/" + quote(object_key, safe="/~")
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["GET", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(settings.obs_secret_key, date_stamp, region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            "AWS4-HMAC-SHA256 "
            f"Credential={settings.obs_access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    return LoadedFile(
        content=response.content,
        content_type=response.headers.get("content-type") or _content_type(None, Path(object_key).suffix.lower()),
    )


async def save_upload_bytes(
    content: bytes,
    filename: str | None,
    content_type: str | None,
    folder: str,
) -> StoredFile:
    suffix = _safe_suffix(filename)
    month = datetime.now().strftime("%Y%m")
    object_key = f"{folder.strip('/')}/{month}/{uuid4().hex}{suffix}"
    mime = _content_type(content_type, suffix)
    if settings.storage_driver.lower() == "obs":
        missing = _missing_obs_config()
        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"OBS 上传配置不完整，缺少：{', '.join(missing)}",
            )
        return await _put_obs_object(content, object_key, mime)

    target = Path(settings.upload_dir) / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    relative_path = f"/uploads/{object_key}"
    return StoredFile(url=relative_path, path=relative_path, storage="local", bucket="", object_key=object_key)


async def load_upload_bytes(object_key: str, storage: str | None = None) -> LoadedFile:
    clean_key = object_key.strip().lstrip("/")
    if not clean_key or ".." in Path(clean_key).parts:
        raise HTTPException(status_code=400, detail="图片路径不合法")
    driver = (storage or settings.storage_driver).lower()
    if driver == "obs":
        missing = _missing_obs_config()
        if missing:
            raise HTTPException(status_code=500, detail=f"OBS 读取配置不完整，缺少：{', '.join(missing)}")
        return await _get_obs_object(clean_key)

    target = Path(settings.upload_dir) / clean_key
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return LoadedFile(content=target.read_bytes(), content_type=_content_type(None, target.suffix.lower()))
