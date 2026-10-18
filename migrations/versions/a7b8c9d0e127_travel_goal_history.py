"""add travel goal history

Revision ID: a7b8c9d0e127
Revises: a7b8c9d0e126
Create Date: 2026-10-18 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e127"
down_revision: Union[str, None] = "a7b8c9d0e126"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "travel_goal_histories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False, server_default="旅行目标"),
        sa.Column("location", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("place_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "request_key", name="uq_travel_goal_user_request"),
    )
    op.create_index("ix_travel_goal_histories_user_id", "travel_goal_histories", ["user_id"])
    op.create_index("ix_travel_goal_histories_request_key", "travel_goal_histories", ["request_key"])


def downgrade() -> None:
    op.drop_index("ix_travel_goal_histories_request_key", table_name="travel_goal_histories")
    op.drop_index("ix_travel_goal_histories_user_id", table_name="travel_goal_histories")
    op.drop_table("travel_goal_histories")
