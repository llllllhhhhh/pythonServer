"""add vip-only article access

Revision ID: a7b8c9d0e128
Revises: a7b8c9d0e127
Create Date: 2026-10-18 20:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e128"
down_revision: Union[str, None] = "a7b8c9d0e127"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("content_articles", sa.Column("vip_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_content_articles_vip_only", "content_articles", ["vip_only"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_content_articles_vip_only", table_name="content_articles")
    op.drop_column("content_articles", "vip_only")
