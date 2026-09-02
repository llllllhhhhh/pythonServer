"""stock sync, timeout job flag, school unique constraints

Revision ID: f6a3b4c5d602
Revises: f5c2d8a9e731
Create Date: 2026-08-24 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f6a3b4c5d602"
down_revision: Union[str, None] = "f5c2d8a9e731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dedupe_school_sites(bind) -> None:
    """Make merchant_account unique and (name, city) unique before adding
    constraints: keep the smallest id per duplicate group and suffix the
    merchant_account of later duplicates so no constraint is violated."""
    if bind.dialect.name == "mysql":
        bind.execute(
            sa.text(
                """
                UPDATE school_sites s
                JOIN (
                    SELECT MIN(id) AS keep_id, merchant_account
                    FROM school_sites
                    WHERE merchant_account <> ''
                    GROUP BY merchant_account
                    HAVING COUNT(*) > 1
                ) d ON s.merchant_account = d.merchant_account AND s.id <> d.keep_id
                SET s.merchant_account = CONCAT(LEFT(s.merchant_account, 50), '-dup-', s.id)
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE school_sites s
                JOIN (
                    SELECT MIN(id) AS keep_id, name, city
                    FROM school_sites
                    GROUP BY name, city
                    HAVING COUNT(*) > 1
                ) d ON s.name = d.name AND s.city = d.city AND s.id <> d.keep_id
                SET s.name = CONCAT(LEFT(s.name, 140), ' (duplicate-', s.id, ')')
                """
            )
        )


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return column_name in {item["name"] for item in sa.inspect(bind).get_columns(table_name)}


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    return index_name in {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}


def _unique_exists(bind, table_name: str, constraint_name: str) -> bool:
    return constraint_name in {
        item["name"] for item in sa.inspect(bind).get_unique_constraints(table_name)
    }


def upgrade() -> None:
    bind = op.get_bind()
    # V10: flag orders whose delayed timeout job could not be scheduled,
    # so a sweep task can still auto-cancel them.
    if not _column_exists(bind, "commerce_orders", "timeout_job_scheduled"):
        op.add_column(
            "commerce_orders",
            sa.Column("timeout_job_scheduled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )
    if not _index_exists(bind, "commerce_orders", "ix_commerce_orders_timeout_job_scheduled"):
        op.create_index(
            "ix_commerce_orders_timeout_job_scheduled",
            "commerce_orders",
            ["timeout_job_scheduled"],
        )

    # Empty means "not bound" in the API. Store it as NULL so the unique
    # constraint permits multiple schools without merchant accounts.
    with op.batch_alter_table("school_sites") as batch:
        batch.alter_column(
            "merchant_account",
            existing_type=sa.String(length=60),
            nullable=True,
            existing_server_default=None,
        )
    bind.execute(sa.text("UPDATE school_sites SET merchant_account = NULL WHERE merchant_account = ''"))

    _dedupe_school_sites(bind)

    # Batch mode works on both MySQL (forwards) and SQLite (copy).
    if not _unique_exists(bind, "school_sites", "uq_school_sites_merchant_account"):
        with op.batch_alter_table("school_sites") as batch:
            batch.create_unique_constraint("uq_school_sites_merchant_account", ["merchant_account"])
    if not _unique_exists(bind, "school_sites", "uq_school_sites_name_city"):
        with op.batch_alter_table("school_sites") as batch:
            batch.create_unique_constraint("uq_school_sites_name_city", ["name", "city"])


def downgrade() -> None:
    with op.batch_alter_table("school_sites") as batch:
        batch.drop_constraint("uq_school_sites_name_city", type_="unique")
        batch.drop_constraint("uq_school_sites_merchant_account", type_="unique")
    op.drop_index("ix_commerce_orders_timeout_job_scheduled", table_name="commerce_orders")
    op.drop_column("commerce_orders", "timeout_job_scheduled")
