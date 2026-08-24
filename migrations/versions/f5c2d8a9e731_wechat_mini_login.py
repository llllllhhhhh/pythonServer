"""wechat mini program login

Revision ID: f5c2d8a9e731
Revises: e4a9b2c7d018
Create Date: 2026-08-16 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f5c2d8a9e731"
down_revision: Union[str, None] = "e4a9b2c7d018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_accounts", sa.Column("wechat_openid", sa.String(length=64), nullable=True))
    op.create_index("ix_user_accounts_wechat_openid", "user_accounts", ["wechat_openid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_accounts_wechat_openid", table_name="user_accounts")
    op.drop_column("user_accounts", "wechat_openid")
