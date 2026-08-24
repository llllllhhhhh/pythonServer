from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from app.core.config import settings
from app.core.legacy_schema import BASELINE_REVISION, adopt_legacy_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def _database_url() -> sa.URL:
    return sa.URL.create(
        drivername="mysql+pymysql",
        username=settings.mysql_user,
        password=settings.mysql_password,
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        query={"charset": "utf8mb4"},
    )


def _legacy_adoption_allowed() -> bool:
    return settings.allow_legacy_schema_adoption or settings.environment.lower() not in {
        "production",
        "prod",
    }


def migrate_database() -> None:
    config = _alembic_config()
    engine = sa.create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            tables = set(sa.inspect(connection).get_table_names())
            current_heads = set(MigrationContext.configure(connection).get_current_heads())

        if current_heads or not (tables - {"alembic_version"}):
            command.upgrade(config, "head")
            return

        if not _legacy_adoption_allowed():
            raise RuntimeError(
                "An unversioned legacy database was detected. Back it up, set "
                "ALLOW_LEGACY_SCHEMA_ADOPTION=true for the one-time migration, "
                "then restore the setting to false."
            )

        with engine.begin() as connection:
            adopted_tables = adopt_legacy_schema(connection)
        print(
            "Adopted unversioned legacy schema: "
            + ", ".join(sorted(adopted_tables)),
            flush=True,
        )
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    finally:
        engine.dispose()


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    migrate_database()
