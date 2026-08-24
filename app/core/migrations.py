from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def expected_database_heads() -> set[str]:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def current_database_heads() -> set[str]:
    async with engine.connect() as connection:
        return set(
            await connection.run_sync(
                lambda sync_connection: MigrationContext.configure(sync_connection).get_current_heads()
            )
        )


async def ensure_database_current() -> None:
    expected = expected_database_heads()
    current = await current_database_heads()
    if current != expected:
        current_text = ", ".join(sorted(current)) or "<none>"
        expected_text = ", ".join(sorted(expected)) or "<none>"
        raise RuntimeError(
            "Database schema is not current. "
            f"Current revision: {current_text}; expected: {expected_text}. "
            "Run `alembic upgrade head` before starting the service."
        )
