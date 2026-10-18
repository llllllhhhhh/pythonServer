"""add independent user vip fields

Revision ID: a7b8c9d0e125
Revises: b8c9d0e1f235
Create Date: 2026-10-18 15:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e125"
down_revision: Union[str, None] = "b8c9d0e1f235"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_accounts", sa.Column("vip_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_accounts", sa.Column("vip_type", sa.String(length=30), nullable=False, server_default=""))
    op.add_column("user_accounts", sa.Column("vip_started_at", sa.DateTime(), nullable=True))
    op.add_column("user_accounts", sa.Column("vip_expires_at", sa.DateTime(), nullable=True))
    op.add_column("user_accounts", sa.Column("vip_remark", sa.String(length=255), nullable=False, server_default=""))
    op.create_index("ix_user_accounts_vip_enabled", "user_accounts", ["vip_enabled"], unique=False)
    op.create_index("ix_user_accounts_vip_expires_at", "user_accounts", ["vip_expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_accounts_vip_expires_at", table_name="user_accounts")
    op.drop_index("ix_user_accounts_vip_enabled", table_name="user_accounts")
    op.drop_column("user_accounts", "vip_remark")
    op.drop_column("user_accounts", "vip_expires_at")
    op.drop_column("user_accounts", "vip_started_at")
    op.drop_column("user_accounts", "vip_type")
    op.drop_column("user_accounts", "vip_enabled")
