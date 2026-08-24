from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection

import app.models  # noqa: F401
from app.core.database import Base


BASELINE_REVISION = "aa1a7986f555"
LEGACY_REQUIRED_COLUMNS = {
    "support_conversations": {"id", "user_id", "status", "last_message"},
    "support_messages": {"id", "conversation_id", "sender_role", "content"},
    "travel_orders": {"id", "order_no", "order_type", "title", "status"},
    "travel_routes": {"id", "name", "price", "stock", "status"},
}
POST_BASELINE_COLUMNS = {
    "travel_orders": {
        "idempotency_key",
        "points_cost",
        "points_refunded",
        "stock_restored",
        "qr_expires_at",
        "cancelled_at",
        "cancel_reason",
    }
}


class LegacySchemaError(RuntimeError):
    pass


def _user_tables(connection: Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names()) - {"alembic_version"}


def validate_legacy_schema(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    tables = _user_tables(connection)
    if not tables:
        raise LegacySchemaError("The database is empty and does not need legacy adoption")

    unknown = tables - set(Base.metadata.tables)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise LegacySchemaError(f"The unversioned database contains unknown tables: {names}")

    missing_tables = set(LEGACY_REQUIRED_COLUMNS) - tables
    if missing_tables:
        names = ", ".join(sorted(missing_tables))
        raise LegacySchemaError(f"The database does not match a supported legacy schema; missing tables: {names}")

    for table_name, expected_columns in LEGACY_REQUIRED_COLUMNS.items():
        actual_columns = {item["name"] for item in inspector.get_columns(table_name)}
        missing_columns = expected_columns - actual_columns
        if missing_columns:
            names = ", ".join(sorted(missing_columns))
            raise LegacySchemaError(
                f"The database does not match a supported legacy schema; "
                f"{table_name} is missing columns: {names}"
            )
    return tables


def _default_value(column: sa.Column[Any]) -> Any:
    default = column.default
    if default is not None:
        if default.is_scalar:
            return default.arg
        if default.is_callable:
            try:
                return default.arg(None)
            except TypeError:
                return default.arg()

    column_type = column.type
    if isinstance(column_type, sa.Boolean):
        return False
    if isinstance(column_type, sa.Integer):
        return 0
    if isinstance(column_type, sa.Numeric):
        return Decimal("0")
    if isinstance(column_type, sa.JSON):
        return {}
    if isinstance(column_type, (sa.String, sa.Text)):
        return ""
    if isinstance(column_type, (sa.Date, sa.DateTime)):
        return datetime.now()
    raise LegacySchemaError(
        f"No safe legacy value is defined for required column {column.table.name}.{column.name}"
    )


def _add_missing_columns(
    connection: Connection,
    operations: Operations,
    preexisting_tables: set[str],
) -> None:
    required_alters: dict[str, list[sa.Column[Any]]] = {}

    for table_name in sorted(preexisting_tables):
        model_table = Base.metadata.tables[table_name]
        existing_columns = {
            item["name"] for item in sa.inspect(connection).get_columns(table_name)
        }
        excluded = POST_BASELINE_COLUMNS.get(table_name, set())
        for model_column in model_table.columns:
            if model_column.name in existing_columns or model_column.name in excluded:
                continue
            operations.add_column(
                table_name,
                sa.Column(model_column.name, model_column.type, nullable=True),
            )
            if not model_column.nullable:
                target = sa.table(
                    table_name,
                    sa.column(model_column.name, model_column.type),
                )
                connection.execute(
                    target.update()
                    .where(target.c[model_column.name].is_(None))
                    .values({model_column.name: _default_value(model_column)})
                )
                required_alters.setdefault(table_name, []).append(model_column)

    for table_name, columns in required_alters.items():
        with operations.batch_alter_table(table_name) as batch:
            for column in columns:
                batch.alter_column(
                    column.name,
                    existing_type=column.type,
                    nullable=False,
                )


def _create_missing_indexes(
    connection: Connection,
    operations: Operations,
    preexisting_tables: set[str],
) -> None:
    for table_name in sorted(preexisting_tables):
        model_table = Base.metadata.tables[table_name]
        actual_columns = {
            item["name"] for item in sa.inspect(connection).get_columns(table_name)
        }
        existing_indexes = {
            item["name"] for item in sa.inspect(connection).get_indexes(table_name)
        }
        for index in model_table.indexes:
            column_names = [column.name for column in index.columns]
            if not index.name or index.name in existing_indexes:
                continue
            if any(column_name not in actual_columns for column_name in column_names):
                continue
            operations.create_index(
                index.name,
                table_name,
                column_names,
                unique=index.unique,
            )


def _backfill_legacy_data(connection: Connection) -> None:
    if connection.dialect.name == "mysql":
        connection.execute(
            sa.text(
                """
                UPDATE travel_orders target
                JOIN travel_routes route ON target.title = route.name
                SET target.route_id = route.id
                WHERE target.route_id = 0
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE travel_orders target
                JOIN travel_routes route ON target.route_id = route.id
                SET target.cover = route.image
                WHERE (target.cover = '' OR target.cover IS NULL)
                  AND route.image <> ''
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE support_messages
                SET extra = content
                WHERE message_type = 'order_card'
                  AND (extra IS NULL OR JSON_TYPE(extra) = 'NULL')
                  AND JSON_VALID(content)
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO contract_template_snapshots
                    (version, title, content, travel_date_days, content_hash)
                SELECT
                    1,
                    COALESCE(JSON_UNQUOTE(JSON_EXTRACT(value, '$.title')), '旅行服务合同'),
                    COALESCE(JSON_UNQUOTE(JSON_EXTRACT(value, '$.content')), ''),
                    COALESCE(JSON_EXTRACT(value, '$.travel_date_days'), 30),
                    SHA2(CONCAT_WS('\n',
                        COALESCE(JSON_UNQUOTE(JSON_EXTRACT(value, '$.title')), '旅行服务合同'),
                        COALESCE(JSON_UNQUOTE(JSON_EXTRACT(value, '$.content')), ''),
                        COALESCE(JSON_EXTRACT(value, '$.travel_date_days'), 30)
                    ), 256)
                FROM system_settings
                WHERE `key` = 'travel.contract_template'
                  AND NOT EXISTS (SELECT 1 FROM contract_template_snapshots)
                """
            )
        )
    else:
        connection.execute(
            sa.text(
                """
                UPDATE travel_orders
                SET route_id = COALESCE((
                    SELECT route.id FROM travel_routes route
                    WHERE route.name = travel_orders.title LIMIT 1
                ), 0)
                WHERE route_id = 0
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE travel_orders
                SET cover = COALESCE((
                    SELECT route.image FROM travel_routes route
                    WHERE route.id = travel_orders.route_id LIMIT 1
                ), '')
                WHERE cover = '' OR cover IS NULL
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE support_messages
                SET extra = content
                WHERE message_type = 'order_card'
                  AND extra IS NULL
                  AND json_valid(content)
                """
            )
        )

    connection.execute(
        sa.text(
            """
            UPDATE travel_orders
            SET contract_template_version = 1,
                contract_template_snapshot_id = (
                    SELECT id FROM contract_template_snapshots
                    WHERE version = 1 LIMIT 1
                )
            WHERE contract_status <> 'unsigned'
              AND contract_template_snapshot_id = 0
              AND EXISTS (
                  SELECT 1 FROM contract_template_snapshots WHERE version = 1
              )
            """
        )
    )


def adopt_legacy_schema(connection: Connection) -> set[str]:
    preexisting_tables = validate_legacy_schema(connection)
    Base.metadata.create_all(connection, checkfirst=True)

    operations = Operations(MigrationContext.configure(connection))
    _add_missing_columns(connection, operations, preexisting_tables)
    _create_missing_indexes(connection, operations, preexisting_tables)
    _backfill_legacy_data(connection)
    return preexisting_tables
