from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_version: str = "1.1.0"
    build_commit: str = "development"
    app_name: str = "学徒行 API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    app_secret_key: str = "development-only-change-me-32-bytes"
    asset_url_ttl_seconds: int = 900
    wechat_app_id: str = ""
    wechat_mini_app_id: str = ""
    wechat_mini_app_secret: str = ""
    wechat_mch_id: str = ""
    wechat_api_v3_key: str = ""
    wechat_notify_url: str = ""
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "xuetuxing"
    mysql_password: str = "xuetuxing123"
    mysql_database: str = "xuetuxing"
    allow_legacy_schema_adoption: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = True
    order_idempotency_ttl_seconds: int = 600
    order_payment_timeout_minutes: int = 30
    login_max_attempts: int = 5
    login_attempt_window_seconds: int = 900
    trusted_proxy_ips: str = "127.0.0.1,::1"
    snowflake_worker_id: int = 1
    snowflake_datacenter_id: int = 1
    cors_origins: str = "http://localhost:5178,http://127.0.0.1:5178"
    upload_dir: str = "uploads"
    storage_driver: str = "local"
    obs_access_key: str = ""
    obs_secret_key: str = ""
    obs_bucket: str = ""
    obs_region: str = "cn-north-4"
    obs_endpoint: str = ""
    obs_public_base_url: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_runtime_requirements(self) -> "Settings":
        self.environment = self.environment.strip().lower()
        if not self.redis_enabled:
            raise ValueError("REDIS_ENABLED must be true. Standard order idempotency and stock locking require Redis.")
        if self.asset_url_ttl_seconds < 60 or self.asset_url_ttl_seconds > 3600:
            raise ValueError("ASSET_URL_TTL_SECONDS must be between 60 and 3600")
        if self.environment.lower() in {"production", "prod"}:
            if bool(self.wechat_mini_app_id) != bool(self.wechat_mini_app_secret):
                raise ValueError("WECHAT_MINI_APP_ID and WECHAT_MINI_APP_SECRET must be configured together")
            if len(self.app_secret_key) < 32 or self.app_secret_key.startswith("development-only"):
                raise ValueError("APP_SECRET_KEY must be a production secret with at least 32 characters")
            if not self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must contain at least one HTTPS origin in production")
            if self.cors_origins.strip() == "*":
                raise ValueError("CORS_ORIGINS cannot be '*' in production")
            if any(not origin.startswith("https://") for origin in self.cors_origin_list):
                raise ValueError("CORS_ORIGINS must use HTTPS in production")
            if self.mysql_password in {"", "xuetuxing123", "root123456"}:
                raise ValueError("MYSQL_PASSWORD must not use a development default in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_ips.split(",") if item.strip()]

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
