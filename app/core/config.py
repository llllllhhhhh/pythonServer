from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_name: str = "学徒行 API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    admin_api_key: str = "xuetuxing-dev-key"
    wechat_app_id: str = ""
    wechat_mch_id: str = ""
    wechat_api_v3_key: str = ""
    wechat_notify_url: str = ""
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "xuetuxing"
    mysql_password: str = "xuetuxing123"
    mysql_database: str = "xuetuxing"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = False
    cors_origins: str = "http://localhost:5178,http://127.0.0.1:5178,http://113.44.149.128"
    upload_dir: str = "uploads"
    default_admin_phone: str = "13800000000"
    default_admin_password: str = "admin123456"
    default_admin_nickname: str = "学徒行管理员"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

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
