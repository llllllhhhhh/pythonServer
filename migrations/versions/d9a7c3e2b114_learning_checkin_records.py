"""learning checkin records

Revision ID: d9a7c3e2b114
Revises: c8e3d4f5a621
Create Date: 2026-07-25 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9a7c3e2b114"
down_revision: Union[str, None] = "c8e3d4f5a621"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_checkin_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("checkin_date", sa.String(length=10), nullable=False),
        sa.Column("checkin_at", sa.DateTime(), nullable=False),
        sa.Column("checkin_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_checkin_records_user_id", "learning_checkin_records", ["user_id"])
    op.create_index("ix_learning_checkin_records_checkin_date", "learning_checkin_records", ["checkin_date"])
    op.create_index("ix_learning_checkin_records_checkin_at", "learning_checkin_records", ["checkin_at"])
    op.create_index(
        "uq_learning_checkin_records_user_date",
        "learning_checkin_records",
        ["user_id", "checkin_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_learning_checkin_records_user_date", table_name="learning_checkin_records")
    op.drop_index("ix_learning_checkin_records_checkin_at", table_name="learning_checkin_records")
    op.drop_index("ix_learning_checkin_records_checkin_date", table_name="learning_checkin_records")
    op.drop_index("ix_learning_checkin_records_user_id", table_name="learning_checkin_records")
    op.drop_table("learning_checkin_records")
