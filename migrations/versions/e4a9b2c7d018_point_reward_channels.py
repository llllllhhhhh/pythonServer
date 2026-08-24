"""point reward channels

Revision ID: e4a9b2c7d018
Revises: d9a7c3e2b114
Create Date: 2026-07-26 20:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4a9b2c7d018"
down_revision: Union[str, None] = "d9a7c3e2b114"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("point_rules", sa.Column("checkin_score", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("point_rules", sa.Column("purchase_score", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("point_rules", sa.Column("invite_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("point_rules", sa.Column("checkin_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("point_rules", sa.Column("purchase_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("point_rules", "purchase_enabled")
    op.drop_column("point_rules", "checkin_enabled")
    op.drop_column("point_rules", "invite_enabled")
    op.drop_column("point_rules", "purchase_score")
    op.drop_column("point_rules", "checkin_score")
