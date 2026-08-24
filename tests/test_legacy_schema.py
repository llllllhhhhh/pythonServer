from __future__ import annotations

import unittest

import sqlalchemy as sa

from app.core.database import Base
from app.core.legacy_schema import LegacySchemaError, adopt_legacy_schema, validate_legacy_schema


class LegacySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = sa.create_engine("sqlite:///:memory:")

    def tearDown(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _create_legacy_schema(connection: sa.Connection) -> None:
        statements = (
            """
            CREATE TABLE support_conversations (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL,
                last_message VARCHAR(255) NOT NULL
            )
            """,
            """
            CREATE TABLE support_messages (
                id INTEGER PRIMARY KEY,
                conversation_id VARCHAR(36) NOT NULL,
                sender_role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                message_type VARCHAR(20) NOT NULL
            )
            """,
            """
            CREATE TABLE travel_routes (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                price NUMERIC(10, 2) NOT NULL,
                stock INTEGER NOT NULL,
                image TEXT NOT NULL,
                status BOOLEAN NOT NULL
            )
            """,
            """
            CREATE TABLE travel_orders (
                id INTEGER PRIMARY KEY,
                order_no VARCHAR(40) NOT NULL UNIQUE,
                order_type VARCHAR(30) NOT NULL,
                title VARCHAR(160) NOT NULL,
                status INTEGER NOT NULL
            )
            """,
        )
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO travel_routes (id, name, price, stock, image, status) "
            "VALUES (7, '历史路线', 1200, 3, '/route.jpg', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO travel_orders (id, order_no, order_type, title, status) "
            "VALUES (9, 'TR-LEGACY', 'points_exchange', '历史路线', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO support_conversations (id, user_id, status, last_message) "
            "VALUES ('legacy-chat', 'U001', 'open', '历史订单')"
        )
        connection.exec_driver_sql(
            "INSERT INTO support_messages "
            "(id, conversation_id, sender_role, content, message_type) "
            "VALUES (3, 'legacy-chat', 'user', '{\"order_no\":\"TR-LEGACY\"}', 'order_card')"
        )

    def test_adoption_preserves_rows_and_backfills_snapshots(self) -> None:
        with self.engine.begin() as connection:
            self._create_legacy_schema(connection)
            adopted = adopt_legacy_schema(connection)

            self.assertEqual(
                {
                    "support_conversations",
                    "support_messages",
                    "travel_orders",
                    "travel_routes",
                },
                adopted,
            )
            self.assertEqual(set(Base.metadata.tables), set(sa.inspect(connection).get_table_names()))

            order = connection.execute(
                sa.text(
                    "SELECT order_no, route_id, cover FROM travel_orders WHERE id = 9"
                )
            ).mappings().one()
            self.assertEqual("TR-LEGACY", order["order_no"])
            self.assertEqual(7, order["route_id"])
            self.assertEqual("/route.jpg", order["cover"])

            message = connection.execute(
                sa.text("SELECT extra FROM support_messages WHERE id = 3")
            ).scalar_one()
            self.assertIn("TR-LEGACY", message)

            order_columns = {
                item["name"] for item in sa.inspect(connection).get_columns("travel_orders")
            }
            self.assertIn("contract_signature_url", order_columns)
            self.assertNotIn("idempotency_key", order_columns)

    def test_unknown_tables_are_rejected(self) -> None:
        with self.engine.begin() as connection:
            self._create_legacy_schema(connection)
            connection.exec_driver_sql("CREATE TABLE foreign_table (id INTEGER PRIMARY KEY)")
            with self.assertRaisesRegex(LegacySchemaError, "unknown tables: foreign_table"):
                validate_legacy_schema(connection)


if __name__ == "__main__":
    unittest.main()
