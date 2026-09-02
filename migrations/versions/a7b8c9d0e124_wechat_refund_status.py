"""wechat refund lifecycle fields

Revision ID: a7b8c9d0e124
Revises: f6a3b4c5d602
Create Date: 2026-09-02 19:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e124"
down_revision: Union[str, None] = "f6a3b4c5d602"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("commerce_orders", sa.Column("refund_no", sa.String(length=64), nullable=True))
    op.add_column(
        "commerce_orders",
        sa.Column("refund_id", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column(
        "commerce_orders",
        sa.Column("refund_status", sa.String(length=20), nullable=False, server_default=""),
    )
    op.add_column("commerce_orders", sa.Column("refunded_at", sa.DateTime(), nullable=True))
    op.create_index("ix_commerce_orders_refund_no", "commerce_orders", ["refund_no"], unique=True)
    op.create_index("ix_commerce_orders_refund_status", "commerce_orders", ["refund_status"])


def downgrade() -> None:
    op.drop_index("ix_commerce_orders_refund_status", table_name="commerce_orders")
    op.drop_index("ix_commerce_orders_refund_no", table_name="commerce_orders")
    op.drop_column("commerce_orders", "refunded_at")
    op.drop_column("commerce_orders", "refund_status")
    op.drop_column("commerce_orders", "refund_id")
    op.drop_column("commerce_orders", "refund_no")
