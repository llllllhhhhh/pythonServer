"""add vip purchase point rule

Revision ID: a7b8c9d0e126
Revises: a7b8c9d0e125
Create Date: 2026-10-18 16:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e126"
down_revision: Union[str, None] = "a7b8c9d0e125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("point_rules", sa.Column("vip_purchase_score", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("point_rules", sa.Column("vip_purchase_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("point_rules", "vip_purchase_enabled")
    op.drop_column("point_rules", "vip_purchase_score")
