"""user refund request fields

Revision ID: b8c9d0e1f235
Revises: a7b8c9d0e124
Create Date: 2026-09-02 22:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8c9d0e1f235"
down_revision: Union[str, None] = "a7b8c9d0e124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "commerce_orders",
        sa.Column("refund_reason", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column("commerce_orders", sa.Column("refund_requested_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("commerce_orders", "refund_requested_at")
    op.drop_column("commerce_orders", "refund_reason")
