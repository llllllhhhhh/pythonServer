"""add point ledger and travel lifecycle audit

Revision ID: b7f2c8d9e410
Revises: aa1a7986f555
Create Date: 2026-07-25 18:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b7f2c8d9e410"
down_revision: str | None = "aa1a7986f555"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_INDEXES = {
    "school_sites": {
        "ix_school_sites_display_weight": ["display_weight"],
        "ix_school_sites_merchant_account": ["merchant_account"],
        "ix_school_sites_review_status": ["review_status"],
    },
    "study_orders": {"ix_study_orders_school_id": ["school_id"]},
    "study_products": {
        "ix_study_products_review_status": ["review_status"],
        "ix_study_products_school_id": ["school_id"],
    },
    "support_conversations": {
        "ix_support_conversations_conversation_type": ["conversation_type"],
        "ix_support_conversations_merchant_online": ["merchant_online"],
        "ix_support_conversations_order_id": ["order_id"],
        "ix_support_conversations_order_no": ["order_no"],
        "ix_support_conversations_product_id": ["product_id"],
        "ix_support_conversations_school_id": ["school_id"],
    },
    "travel_orders": {
        "ix_travel_orders_contract_status": ["contract_status"],
        "ix_travel_orders_contract_template_snapshot_id": ["contract_template_snapshot_id"],
        "ix_travel_orders_contract_template_version": ["contract_template_version"],
        "ix_travel_orders_fulfillment_status": ["fulfillment_status"],
        "ix_travel_orders_qr_token": ["qr_token"],
        "ix_travel_orders_user_id": ["user_id"],
        "ix_travel_orders_user_no": ["user_no"],
    },
    "travel_routes": {
        "ix_travel_routes_display_weight": ["display_weight"],
        "ix_travel_routes_review_status": ["review_status"],
        "ix_travel_routes_school_id": ["school_id"],
    },
}


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _unique_exists(table_name: str, constraint_name: str) -> bool:
    return any(item["name"] == constraint_name for item in _inspector().get_unique_constraints(table_name))


def _add_column(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, str(column.name)):
        op.add_column(table_name, column)


def _add_index(table_name: str, index_name: str, columns: list[str]) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _make_nullable(table_name: str, column_name: str) -> None:
    column = next(item for item in _inspector().get_columns(table_name) if item["name"] == column_name)
    if not column["nullable"]:
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(column_name, existing_type=column["type"], nullable=True)


def _create_point_tables() -> None:
    if not _table_exists("point_ledgers"):
        op.create_table(
            "point_ledgers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("transaction_no", sa.String(50), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("user_no", sa.String(64), nullable=False, server_default=""),
            sa.Column("direction", sa.String(20), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("balance_before", sa.Integer(), nullable=False),
            sa.Column("balance_after", sa.Integer(), nullable=False),
            sa.Column("biz_type", sa.String(40), nullable=False),
            sa.Column("biz_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("biz_no", sa.String(80), nullable=False, server_default=""),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("remark", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "idempotency_key", name="uq_point_ledgers_user_idempotency"),
        )
        op.create_index("ix_point_ledgers_transaction_no", "point_ledgers", ["transaction_no"], unique=True)
        op.create_index("ix_point_ledgers_user_id", "point_ledgers", ["user_id"])
        op.create_index("ix_point_ledgers_user_no", "point_ledgers", ["user_no"])
        op.create_index("ix_point_ledgers_direction", "point_ledgers", ["direction"])
        op.create_index("ix_point_ledgers_biz_type", "point_ledgers", ["biz_type"])
        op.create_index("ix_point_ledgers_biz_id", "point_ledgers", ["biz_id"])
        op.create_index("ix_point_ledgers_biz_no", "point_ledgers", ["biz_no"])
        op.create_index("ix_point_ledgers_created_at", "point_ledgers", ["created_at"])
        op.create_index("ix_point_ledgers_user_created", "point_ledgers", ["user_id", "created_at"])
        op.create_index("ix_point_ledgers_business", "point_ledgers", ["biz_type", "biz_no"])

    if not _table_exists("point_lots"):
        op.create_table(
            "point_lots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("source_ledger_id", sa.Integer(), nullable=False),
            sa.Column("original_amount", sa.Integer(), nullable=False),
            sa.Column("remaining_amount", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("source_ledger_id", name="uq_point_lots_source_ledger"),
        )
        op.create_index("ix_point_lots_user_id", "point_lots", ["user_id"])
        op.create_index("ix_point_lots_source_ledger_id", "point_lots", ["source_ledger_id"])
        op.create_index("ix_point_lots_expires_at", "point_lots", ["expires_at"])
        op.create_index("ix_point_lots_status", "point_lots", ["status"])
        op.create_index("ix_point_lots_user_expiry", "point_lots", ["user_id", "expires_at"])

    if not _table_exists("point_lot_allocations"):
        op.create_table(
            "point_lot_allocations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("expense_ledger_id", sa.Integer(), nullable=False),
            sa.Column("lot_id", sa.Integer(), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("restored_amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("expense_ledger_id", "lot_id", name="uq_point_lot_allocations_expense_lot"),
        )
        op.create_index("ix_point_lot_allocations_expense_ledger_id", "point_lot_allocations", ["expense_ledger_id"])
        op.create_index("ix_point_lot_allocations_lot_id", "point_lot_allocations", ["lot_id"])


def _create_travel_audit_tables() -> None:
    if not _table_exists("travel_order_audit_logs"):
        op.create_table(
            "travel_order_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("order_no", sa.String(40), nullable=False),
            sa.Column("event_type", sa.String(40), nullable=False),
            sa.Column("from_status", sa.String(40), nullable=False, server_default=""),
            sa.Column("to_status", sa.String(40), nullable=False, server_default=""),
            sa.Column("actor_role", sa.String(20), nullable=False, server_default="system"),
            sa.Column("actor_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actor_name", sa.String(60), nullable=False, server_default=""),
            sa.Column("reason", sa.String(255), nullable=False, server_default=""),
            sa.Column("evidence", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, columns in {
            "ix_travel_order_audit_logs_order_id": ["order_id"],
            "ix_travel_order_audit_logs_order_no": ["order_no"],
            "ix_travel_order_audit_logs_event_type": ["event_type"],
            "ix_travel_order_audit_logs_actor_role": ["actor_role"],
            "ix_travel_order_audit_logs_actor_id": ["actor_id"],
            "ix_travel_order_audit_logs_created_at": ["created_at"],
            "ix_travel_order_audit_order_created": ["order_id", "created_at"],
            "ix_travel_order_audit_event_created": ["event_type", "created_at"],
        }.items():
            op.create_index(name, "travel_order_audit_logs", columns)

    if not _table_exists("travel_verification_records"):
        op.create_table(
            "travel_verification_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("order_no", sa.String(40), nullable=False),
            sa.Column("action", sa.String(30), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("operator_role", sa.String(20), nullable=False, server_default="system"),
            sa.Column("operator_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("operator_name", sa.String(60), nullable=False, server_default=""),
            sa.Column("device", sa.String(255), nullable=False, server_default=""),
            sa.Column("location", sa.String(255), nullable=False, server_default=""),
            sa.Column("reason", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, columns in {
            "ix_travel_verification_records_order_id": ["order_id"],
            "ix_travel_verification_records_order_no": ["order_no"],
            "ix_travel_verification_records_action": ["action"],
            "ix_travel_verification_records_token_hash": ["token_hash"],
            "ix_travel_verification_records_result": ["result"],
            "ix_travel_verification_records_operator_role": ["operator_role"],
            "ix_travel_verification_records_operator_id": ["operator_id"],
            "ix_travel_verification_records_created_at": ["created_at"],
            "ix_travel_verification_order_created": ["order_id", "created_at"],
            "ix_travel_verification_token_result": ["token_hash", "result"],
        }.items():
            op.create_index(name, "travel_verification_records", columns)


def _backfill_legacy_points() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        bind.execute(
            sa.text(
                """
                INSERT IGNORE INTO point_ledgers
                    (transaction_no, user_id, user_no, direction, amount, balance_before, balance_after,
                     biz_type, biz_id, biz_no, idempotency_key, remark)
                SELECT CONCAT('PL-OPEN-', id), id, user_no, 'income', points, 0, points,
                       'legacy_opening', id, user_no, CONCAT('legacy-opening:', id), '历史积分期初余额'
                FROM user_accounts
                WHERE points > 0
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT IGNORE INTO point_lots
                    (user_id, source_ledger_id, original_amount, remaining_amount, expires_at, status)
                SELECT ledger.user_id, ledger.id, ledger.amount, ledger.amount, NULL, 'active'
                FROM point_ledgers ledger
                WHERE ledger.biz_type = 'legacy_opening'
                """
            )
        )
    else:
        bind.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO point_ledgers
                    (transaction_no, user_id, user_no, direction, amount, balance_before, balance_after,
                     biz_type, biz_id, biz_no, idempotency_key, remark)
                SELECT 'PL-OPEN-' || id, id, user_no, 'income', points, 0, points,
                       'legacy_opening', id, user_no, 'legacy-opening:' || id, '历史积分期初余额'
                FROM user_accounts
                WHERE points > 0
                """
            )
        )
        bind.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO point_lots
                    (user_id, source_ledger_id, original_amount, remaining_amount, expires_at, status)
                SELECT ledger.user_id, ledger.id, ledger.amount, ledger.amount, NULL, 'active'
                FROM point_ledgers ledger
                WHERE ledger.biz_type = 'legacy_opening'
                """
            )
        )


def upgrade() -> None:
    for table_name, indexes in LEGACY_INDEXES.items():
        for index_name, columns in indexes.items():
            _add_index(table_name, index_name, columns)

    _make_nullable("support_messages", "image_thumb_url")
    _make_nullable("travel_orders", "contract_signature_data")
    _make_nullable("travel_orders", "contract_signature_url")

    _add_column("travel_orders", sa.Column("idempotency_key", sa.String(120), nullable=False, server_default=""))
    _add_column("travel_orders", sa.Column("points_cost", sa.Integer(), nullable=False, server_default="0"))
    _add_column("travel_orders", sa.Column("points_refunded", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column("travel_orders", sa.Column("stock_restored", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column("travel_orders", sa.Column("qr_expires_at", sa.DateTime(), nullable=True))
    _add_column("travel_orders", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    _add_column("travel_orders", sa.Column("cancel_reason", sa.String(255), nullable=False, server_default=""))

    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        bind.execute(
            sa.text(
                """
                UPDATE travel_orders
                SET idempotency_key = CONCAT('legacy:', order_no)
                WHERE idempotency_key = ''
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE travel_orders
                SET points_cost = CAST(SUBSTRING_INDEX(amount_text, ' ', 1) AS UNSIGNED)
                WHERE order_type = 'points_exchange' AND points_cost = 0
                """
            )
        )
    else:
        bind.execute(sa.text("UPDATE travel_orders SET idempotency_key = 'legacy:' || order_no WHERE idempotency_key = ''"))
        bind.execute(
            sa.text(
                """
                UPDATE travel_orders
                SET points_cost = CAST(CASE WHEN instr(amount_text, ' ') > 0
                    THEN substr(amount_text, 1, instr(amount_text, ' ') - 1)
                    ELSE amount_text END AS INTEGER)
                WHERE order_type = 'points_exchange' AND points_cost = 0
                """
            )
        )

    if not _unique_exists("travel_orders", "uq_travel_orders_user_idempotency"):
        with op.batch_alter_table("travel_orders") as batch:
            batch.create_unique_constraint("uq_travel_orders_user_idempotency", ["user_id", "idempotency_key"])

    for index_name, columns in {
        "ix_travel_orders_points_refunded": ["points_refunded"],
        "ix_travel_orders_stock_restored": ["stock_restored"],
        "ix_travel_orders_qr_expires_at": ["qr_expires_at"],
        "ix_travel_orders_cancelled_at": ["cancelled_at"],
        "ix_travel_orders_user_status_created": ["user_id", "fulfillment_status", "created_at"],
    }.items():
        _add_index("travel_orders", index_name, columns)

    _create_point_tables()
    _create_travel_audit_tables()
    _backfill_legacy_points()


def downgrade() -> None:
    if _unique_exists("travel_orders", "uq_travel_orders_user_idempotency"):
        with op.batch_alter_table("travel_orders") as batch:
            batch.drop_constraint("uq_travel_orders_user_idempotency", type_="unique")
    for index_name in (
        "ix_travel_orders_points_refunded",
        "ix_travel_orders_stock_restored",
        "ix_travel_orders_qr_expires_at",
        "ix_travel_orders_cancelled_at",
        "ix_travel_orders_user_status_created",
    ):
        if _index_exists("travel_orders", index_name):
            op.drop_index(index_name, table_name="travel_orders")
    for table_name in (
        "travel_verification_records",
        "travel_order_audit_logs",
        "point_lot_allocations",
        "point_lots",
        "point_ledgers",
    ):
        if _table_exists(table_name):
            op.drop_table(table_name)
    for column_name in (
        "cancel_reason",
        "cancelled_at",
        "qr_expires_at",
        "stock_restored",
        "points_refunded",
        "points_cost",
        "idempotency_key",
    ):
        if _column_exists("travel_orders", column_name):
            with op.batch_alter_table("travel_orders") as batch:
                batch.drop_column(column_name)
