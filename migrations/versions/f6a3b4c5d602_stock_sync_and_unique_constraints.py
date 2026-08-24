"""stock sync, timeout job flag, school/entitlement unique constraints

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
                SET s.merchant_account = CONCAT(IFNULL(s.merchant_account, ''), 'dup-', s.id)
                """
            )
        )


def _dedupe_entitlements(bind) -> None:
    """Drop duplicate user_entitlements rows for the same order (keep the
    smallest id) so the order_id unique constraint can be created."""
    if bind.dialect.name == "mysql":
        bind.execute(
            sa.text(
                """
                DELETE e FROM user_entitlements e
                JOIN user_entitlements keep
                  ON keep.order_id = e.order_id
                 AND keep.id = (SELECT MIN(id) FROM user_entitlements k2 WHERE k2.order_id = e.order_id)
                WHERE e.id <> keep.id
                """
            )
        )


def upgrade() -> None:
    # V10: flag orders whose delayed timeout job could not be scheduled,
    # so a sweep task can still auto-cancel them.
    op.add_column(
        "commerce_orders",
        sa.Column("timeout_job_scheduled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index(
        "ix_commerce_orders_timeout_job_scheduled",
        "commerce_orders",
        ["timeout_job_scheduled"],
    )

    bind = op.get_bind()
    _dedupe_school_sites(bind)
    _dedupe_entitlements(bind)

    # V12 / V3: batch mode works on both MySQL (forwards) and SQLite (copy).
    with op.batch_alter_table("school_sites") as batch:
        batch.create_unique_constraint("uq_school_sites_merchant_account", ["merchant_account"])
        batch.create_unique_constraint("uq_school_sites_name_city", ["name", "city"])
    with op.batch_alter_table("user_entitlements") as batch:
        batch.create_unique_constraint("uq_user_entitlements_order", ["order_id"])


def downgrade() -> None:
    with op.batch_alter_table("user_entitlements") as batch:
        batch.drop_constraint("uq_user_entitlements_order", type_="unique")
    with op.batch_alter_table("school_sites") as batch:
        batch.drop_constraint("uq_school_sites_name_city", type_="unique")
        batch.drop_constraint("uq_school_sites_merchant_account", type_="unique")
    op.drop_index("ix_commerce_orders_timeout_job_scheduled", table_name="commerce_orders")
    op.drop_column("commerce_orders", "timeout_job_scheduled")
