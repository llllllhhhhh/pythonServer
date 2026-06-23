from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def _column_exists(connection, table_name: str, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"schema": settings.mysql_database, "table_name": table_name, "column_name": column_name},
    )
    return (result.scalar() or 0) > 0


async def run_schema_updates() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_accounts (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_no VARCHAR(64) NOT NULL UNIQUE,
                  phone VARCHAR(30) NOT NULL UNIQUE,
                  nickname VARCHAR(60) NOT NULL DEFAULT '小徒同学',
                  password_hash VARCHAR(255) NOT NULL,
                  role VARCHAR(20) NOT NULL DEFAULT 'user',
                  status VARCHAR(20) NOT NULL DEFAULT 'active',
                  avatar TEXT NOT NULL,
                  points INT NOT NULL DEFAULT 0,
                  exam_status VARCHAR(20) NOT NULL DEFAULT '备考中',
                  is_registered TINYINT(1) NOT NULL DEFAULT 1,
                  last_login_at DATETIME NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_user_accounts_user_no (user_no),
                  INDEX ix_user_accounts_phone (phone),
                  INDEX ix_user_accounts_role (role),
                  INDEX ix_user_accounts_status (status),
                  INDEX ix_user_accounts_is_registered (is_registered)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                  token VARCHAR(128) PRIMARY KEY,
                  user_id INT NOT NULL,
                  role VARCHAR(20) NOT NULL DEFAULT 'user',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  expires_at DATETIME NOT NULL,
                  last_seen_at DATETIME NULL,
                  INDEX ix_user_sessions_user_id (user_id),
                  INDEX ix_user_sessions_role (role),
                  INDEX ix_user_sessions_expires_at (expires_at)
                )
                """
            )
        )

        alter_statements = [
            ("support_conversations", "user_online", "ALTER TABLE support_conversations ADD COLUMN user_online TINYINT(1) NOT NULL DEFAULT 0"),
            ("support_conversations", "admin_online", "ALTER TABLE support_conversations ADD COLUMN admin_online TINYINT(1) NOT NULL DEFAULT 0"),
            ("support_conversations", "last_user_online_at", "ALTER TABLE support_conversations ADD COLUMN last_user_online_at DATETIME NULL"),
            ("support_conversations", "last_admin_online_at", "ALTER TABLE support_conversations ADD COLUMN last_admin_online_at DATETIME NULL"),
            ("support_messages", "image_url", "ALTER TABLE support_messages ADD COLUMN image_url TEXT NOT NULL"),
        ]
        for table_name, column_name, statement in alter_statements:
            if not await _column_exists(connection, table_name, column_name):
                await connection.execute(text(statement))


async def create_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await run_schema_updates()
