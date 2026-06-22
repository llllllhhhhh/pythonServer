from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_name: str = "学徒行 API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    admin_api_key: str = "xuetuxing-dev-key"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "xuetuxing"
    mysql_password: str = "xuetuxing123"
    mysql_database: str = "xuetuxing"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = False
    cors_origins: str = "http://localhost:5178,http://127.0.0.1:5178"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
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
