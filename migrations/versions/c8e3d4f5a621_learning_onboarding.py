"""add versioned learning onboarding profile

Revision ID: c8e3d4f5a621
Revises: b7f2c8d9e410
Create Date: 2026-07-25 20:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8e3d4f5a621"
down_revision: str | None = "b7f2c8d9e410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(
        column["name"] == column_name
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    )


def _add_column(column: sa.Column) -> None:
    if not _column_exists("learning_profiles", str(column.name)):
        op.add_column("learning_profiles", column)


def upgrade() -> None:
    _add_column(sa.Column("school_major_status", sa.String(80), nullable=False, server_default=""))
    _add_column(sa.Column("interest_tags", sa.JSON(), nullable=True))
    _add_column(sa.Column("onboarding_version", sa.Integer(), nullable=False, server_default="0"))
    _add_column(sa.Column("onboarding_answers", sa.JSON(), nullable=True))
    _add_column(sa.Column("onboarding_completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for column_name in (
        "onboarding_completed_at",
        "onboarding_answers",
        "onboarding_version",
        "interest_tags",
        "school_major_status",
    ):
        if _column_exists("learning_profiles", column_name):
            with op.batch_alter_table("learning_profiles") as batch:
                batch.drop_column(column_name)
