from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def _column_exists(connection, table_name: str, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"schema": settings.mysql_database, "table_name": table_name, "column_name": column_name},
    )
    return (result.scalar() or 0) > 0


async def run_schema_updates() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_accounts (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_no VARCHAR(64) NOT NULL UNIQUE,
                  phone VARCHAR(30) NOT NULL UNIQUE,
                  nickname VARCHAR(60) NOT NULL DEFAULT '小徒同学',
                  password_hash VARCHAR(255) NOT NULL,
                  role VARCHAR(20) NOT NULL DEFAULT 'user',
                  status VARCHAR(20) NOT NULL DEFAULT 'active',
                  avatar TEXT NOT NULL,
                  points INT NOT NULL DEFAULT 0,
                  balance DECIMAL(10,2) NOT NULL DEFAULT 0,
                  exam_status VARCHAR(20) NOT NULL DEFAULT '学员',
                  is_registered TINYINT(1) NOT NULL DEFAULT 1,
                  last_login_at DATETIME NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_user_accounts_user_no (user_no),
                  INDEX ix_user_accounts_phone (phone),
                  INDEX ix_user_accounts_role (role),
                  INDEX ix_user_accounts_status (status),
                  INDEX ix_user_accounts_is_registered (is_registered)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                  token VARCHAR(128) PRIMARY KEY,
                  user_id INT NOT NULL,
                  role VARCHAR(20) NOT NULL DEFAULT 'user',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  expires_at DATETIME NOT NULL,
                  last_seen_at DATETIME NULL,
                  INDEX ix_user_sessions_user_id (user_id),
                  INDEX ix_user_sessions_role (role),
                  INDEX ix_user_sessions_expires_at (expires_at)
                )
                """
            )
        )

        alter_statements = [
            ("user_accounts", "balance", "ALTER TABLE user_accounts ADD COLUMN balance DECIMAL(10,2) NOT NULL DEFAULT 0"),
            ("support_conversations", "user_online", "ALTER TABLE support_conversations ADD COLUMN user_online TINYINT(1) NOT NULL DEFAULT 0"),
            ("support_conversations", "admin_online", "ALTER TABLE support_conversations ADD COLUMN admin_online TINYINT(1) NOT NULL DEFAULT 0"),
            ("support_conversations", "merchant_online", "ALTER TABLE support_conversations ADD COLUMN merchant_online TINYINT(1) NOT NULL DEFAULT 0"),
            ("support_conversations", "last_user_online_at", "ALTER TABLE support_conversations ADD COLUMN last_user_online_at DATETIME NULL"),
            ("support_conversations", "last_admin_online_at", "ALTER TABLE support_conversations ADD COLUMN last_admin_online_at DATETIME NULL"),
            ("support_conversations", "last_merchant_online_at", "ALTER TABLE support_conversations ADD COLUMN last_merchant_online_at DATETIME NULL"),
            ("support_conversations", "unread_merchant", "ALTER TABLE support_conversations ADD COLUMN unread_merchant INT NOT NULL DEFAULT 0"),
            ("support_conversations", "conversation_type", "ALTER TABLE support_conversations ADD COLUMN conversation_type VARCHAR(30) NOT NULL DEFAULT 'platform'"),
            ("support_conversations", "order_id", "ALTER TABLE support_conversations ADD COLUMN order_id INT NOT NULL DEFAULT 0"),
            ("support_conversations", "order_no", "ALTER TABLE support_conversations ADD COLUMN order_no VARCHAR(40) NOT NULL DEFAULT ''"),
            ("support_conversations", "product_id", "ALTER TABLE support_conversations ADD COLUMN product_id INT NOT NULL DEFAULT 0"),
            ("support_conversations", "product_name", "ALTER TABLE support_conversations ADD COLUMN product_name VARCHAR(160) NOT NULL DEFAULT ''"),
            ("support_conversations", "school_id", "ALTER TABLE support_conversations ADD COLUMN school_id INT NOT NULL DEFAULT 0"),
            ("support_conversations", "school_name", "ALTER TABLE support_conversations ADD COLUMN school_name VARCHAR(160) NOT NULL DEFAULT ''"),
            ("support_messages", "image_url", "ALTER TABLE support_messages ADD COLUMN image_url TEXT NOT NULL"),
            ("support_messages", "image_thumb_url", "ALTER TABLE support_messages ADD COLUMN image_thumb_url TEXT NULL"),
            ("school_sites", "review_status", "ALTER TABLE school_sites ADD COLUMN review_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
            ("school_sites", "reject_reason", "ALTER TABLE school_sites ADD COLUMN reject_reason VARCHAR(255) NOT NULL DEFAULT ''"),
            ("school_sites", "merchant_account", "ALTER TABLE school_sites ADD COLUMN merchant_account VARCHAR(60) NOT NULL DEFAULT ''"),
            ("school_sites", "merchant_password_hash", "ALTER TABLE school_sites ADD COLUMN merchant_password_hash VARCHAR(255) NOT NULL DEFAULT ''"),
            ("school_sites", "display_weight", "ALTER TABLE school_sites ADD COLUMN display_weight INT NOT NULL DEFAULT 0"),
            ("study_products", "school_id", "ALTER TABLE study_products ADD COLUMN school_id INT NOT NULL DEFAULT 0"),
            ("study_products", "review_status", "ALTER TABLE study_products ADD COLUMN review_status VARCHAR(20) NOT NULL DEFAULT 'approved'"),
            ("study_products", "reject_reason", "ALTER TABLE study_products ADD COLUMN reject_reason VARCHAR(255) NOT NULL DEFAULT ''"),
            ("study_orders", "school_id", "ALTER TABLE study_orders ADD COLUMN school_id INT NOT NULL DEFAULT 0"),
            ("commerce_order_items", "installment_count", "ALTER TABLE commerce_order_items ADD COLUMN installment_count INT NOT NULL DEFAULT 1"),
            ("travel_routes", "display_weight", "ALTER TABLE travel_routes ADD COLUMN display_weight INT NOT NULL DEFAULT 0"),
            ("travel_orders", "user_id", "ALTER TABLE travel_orders ADD COLUMN user_id INT NOT NULL DEFAULT 0"),
            ("travel_orders", "user_no", "ALTER TABLE travel_orders ADD COLUMN user_no VARCHAR(64) NOT NULL DEFAULT ''"),
            ("travel_orders", "contract_status", "ALTER TABLE travel_orders ADD COLUMN contract_status VARCHAR(20) NOT NULL DEFAULT 'unsigned'"),
            ("travel_orders", "contract_signer_name", "ALTER TABLE travel_orders ADD COLUMN contract_signer_name VARCHAR(60) NOT NULL DEFAULT ''"),
            ("travel_orders", "contract_signer_phone", "ALTER TABLE travel_orders ADD COLUMN contract_signer_phone VARCHAR(30) NOT NULL DEFAULT ''"),
            ("travel_orders", "contract_id_no", "ALTER TABLE travel_orders ADD COLUMN contract_id_no VARCHAR(40) NOT NULL DEFAULT ''"),
            ("travel_orders", "contract_signature_data", "ALTER TABLE travel_orders ADD COLUMN contract_signature_data MEDIUMTEXT NULL"),
            ("travel_orders", "contract_signed_at", "ALTER TABLE travel_orders ADD COLUMN contract_signed_at DATETIME NULL"),
            ("travel_orders", "contract_reviewed_at", "ALTER TABLE travel_orders ADD COLUMN contract_reviewed_at DATETIME NULL"),
            ("travel_orders", "contract_reject_reason", "ALTER TABLE travel_orders ADD COLUMN contract_reject_reason VARCHAR(255) NOT NULL DEFAULT ''"),
            ("travel_orders", "fulfillment_status", "ALTER TABLE travel_orders ADD COLUMN fulfillment_status VARCHAR(30) NOT NULL DEFAULT 'contract_pending'"),
            ("travel_orders", "pickup_address", "ALTER TABLE travel_orders ADD COLUMN pickup_address VARCHAR(255) NOT NULL DEFAULT ''"),
            ("travel_orders", "pickup_detail", "ALTER TABLE travel_orders ADD COLUMN pickup_detail VARCHAR(255) NOT NULL DEFAULT ''"),
            ("travel_orders", "traveler_count", "ALTER TABLE travel_orders ADD COLUMN traveler_count INT NOT NULL DEFAULT 1"),
            ("travel_orders", "emergency_contact", "ALTER TABLE travel_orders ADD COLUMN emergency_contact VARCHAR(60) NOT NULL DEFAULT ''"),
            ("travel_orders", "emergency_phone", "ALTER TABLE travel_orders ADD COLUMN emergency_phone VARCHAR(30) NOT NULL DEFAULT ''"),
            ("travel_orders", "luggage_count", "ALTER TABLE travel_orders ADD COLUMN luggage_count INT NOT NULL DEFAULT 0"),
            ("travel_orders", "pickup_note", "ALTER TABLE travel_orders ADD COLUMN pickup_note VARCHAR(500) NOT NULL DEFAULT ''"),
            ("travel_orders", "pickup_time", "ALTER TABLE travel_orders ADD COLUMN pickup_time VARCHAR(60) NOT NULL DEFAULT ''"),
            ("travel_orders", "pickup_location", "ALTER TABLE travel_orders ADD COLUMN pickup_location VARCHAR(255) NOT NULL DEFAULT ''"),
            ("travel_orders", "driver_name", "ALTER TABLE travel_orders ADD COLUMN driver_name VARCHAR(60) NOT NULL DEFAULT ''"),
            ("travel_orders", "driver_phone", "ALTER TABLE travel_orders ADD COLUMN driver_phone VARCHAR(30) NOT NULL DEFAULT ''"),
            ("travel_orders", "vehicle_no", "ALTER TABLE travel_orders ADD COLUMN vehicle_no VARCHAR(40) NOT NULL DEFAULT ''"),
            ("travel_orders", "pickup_notice", "ALTER TABLE travel_orders ADD COLUMN pickup_notice VARCHAR(500) NOT NULL DEFAULT ''"),
            ("travel_orders", "pickup_confirmed_at", "ALTER TABLE travel_orders ADD COLUMN pickup_confirmed_at DATETIME NULL"),
            ("travel_orders", "qr_token", "ALTER TABLE travel_orders ADD COLUMN qr_token VARCHAR(80) NOT NULL DEFAULT ''"),
            ("travel_orders", "qr_issued_at", "ALTER TABLE travel_orders ADD COLUMN qr_issued_at DATETIME NULL"),
            ("travel_orders", "checked_in_at", "ALTER TABLE travel_orders ADD COLUMN checked_in_at DATETIME NULL"),
            ("travel_orders", "completed_at", "ALTER TABLE travel_orders ADD COLUMN completed_at DATETIME NULL"),
            ("travel_orders", "exception_reason", "ALTER TABLE travel_orders ADD COLUMN exception_reason VARCHAR(255) NOT NULL DEFAULT ''"),
        ]
        for table_name, column_name, statement in alter_statements:
            if not await _column_exists(connection, table_name, column_name):
                await connection.execute(text(statement))

        await connection.execute(text("ALTER TABLE school_sites MODIFY reject_reason VARCHAR(255) NOT NULL DEFAULT ''"))
        await connection.execute(text("ALTER TABLE school_sites MODIFY merchant_account VARCHAR(60) NOT NULL DEFAULT ''"))
        await connection.execute(text("ALTER TABLE school_sites MODIFY merchant_password_hash VARCHAR(255) NOT NULL DEFAULT ''"))

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wallet_transactions (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_id INT NOT NULL,
                  user_no VARCHAR(64) NOT NULL,
                  transaction_no VARCHAR(50) NOT NULL UNIQUE,
                  direction VARCHAR(20) NOT NULL,
                  amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                  balance_before DECIMAL(10,2) NOT NULL DEFAULT 0,
                  balance_after DECIMAL(10,2) NOT NULL DEFAULT 0,
                  biz_type VARCHAR(30) NOT NULL DEFAULT '',
                  biz_id INT NOT NULL DEFAULT 0,
                  biz_no VARCHAR(80) NOT NULL DEFAULT '',
                  remark VARCHAR(255) NOT NULL DEFAULT '',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_wallet_transactions_user_id (user_id),
                  INDEX ix_wallet_transactions_user_no (user_no),
                  INDEX ix_wallet_transactions_transaction_no (transaction_no),
                  INDEX ix_wallet_transactions_direction (direction),
                  INDEX ix_wallet_transactions_biz_type (biz_type),
                  INDEX ix_wallet_transactions_biz_id (biz_id),
                  INDEX ix_wallet_transactions_biz_no (biz_no),
                  INDEX ix_wallet_transactions_user_created (user_id, created_at),
                  INDEX ix_wallet_transactions_biz (biz_type, biz_no)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commerce_orders (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  order_no VARCHAR(40) NOT NULL UNIQUE,
                  user_id INT NOT NULL,
                  school_id INT NOT NULL DEFAULT 0,
                  total_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                  payable_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                  payment_method VARCHAR(20) NOT NULL DEFAULT 'balance',
                  payment_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  idempotency_key VARCHAR(120) NOT NULL DEFAULT '',
                  transaction_id VARCHAR(100) NOT NULL DEFAULT '',
                  paid_at DATETIME NULL,
                  canceled_at DATETIME NULL,
                  cancel_reason VARCHAR(255) NOT NULL DEFAULT '',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_commerce_orders_order_no (order_no),
                  INDEX ix_commerce_orders_user_id (user_id),
                  INDEX ix_commerce_orders_school_id (school_id),
                  INDEX ix_commerce_orders_payment_method (payment_method),
                  INDEX ix_commerce_orders_payment_status (payment_status),
                  INDEX ix_commerce_orders_status (status),
                  INDEX ix_commerce_orders_idempotency_key (idempotency_key),
                  INDEX ix_commerce_orders_user_status_created (user_id, status, created_at),
                  INDEX ix_commerce_orders_payment_status_created (payment_status, created_at)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS commerce_order_items (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  order_id INT NOT NULL,
                  order_no VARCHAR(40) NOT NULL,
                  product_id INT NOT NULL,
                  product_name VARCHAR(160) NOT NULL,
                  product_type VARCHAR(30) NOT NULL,
                  school_id INT NOT NULL DEFAULT 0,
                  unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
                  quantity INT NOT NULL DEFAULT 1,
                  total_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                  installment_count INT NOT NULL DEFAULT 1,
                  stock_deducted TINYINT(1) NOT NULL DEFAULT 0,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_commerce_order_items_order_id (order_id),
                  INDEX ix_commerce_order_items_order_no (order_no),
                  INDEX ix_commerce_order_items_product_id (product_id),
                  INDEX ix_commerce_order_items_product_type (product_type),
                  INDEX ix_commerce_order_items_school_id (school_id),
                  INDEX ix_commerce_order_items_stock_deducted (stock_deducted),
                  INDEX ix_commerce_order_items_status (status),
                  INDEX ix_commerce_order_items_order_product (order_id, product_id),
                  INDEX ix_commerce_order_items_product_status (product_id, status)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS school_merchant_sessions (
                  token VARCHAR(128) PRIMARY KEY,
                  school_id INT NOT NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  expires_at DATETIME NOT NULL,
                  last_seen_at DATETIME NULL,
                  INDEX ix_school_merchant_sessions_school_id (school_id),
                  INDEX ix_school_merchant_sessions_expires_at (expires_at)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS custom_travel_requests (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  request_no VARCHAR(40) NOT NULL UNIQUE,
                  user_id INT NOT NULL,
                  user_no VARCHAR(64) NOT NULL,
                  user_name VARCHAR(60) NOT NULL DEFAULT '',
                  phone VARCHAR(30) NOT NULL DEFAULT '',
                  destination VARCHAR(160) NOT NULL DEFAULT '',
                  travel_time VARCHAR(80) NOT NULL DEFAULT '',
                  days VARCHAR(40) NOT NULL DEFAULT '',
                  budget VARCHAR(80) NOT NULL DEFAULT '',
                  people_count VARCHAR(40) NOT NULL DEFAULT '',
                  special_tags JSON NULL,
                  note TEXT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  reject_reason VARCHAR(255) NOT NULL DEFAULT '',
                  plan_title VARCHAR(160) NOT NULL DEFAULT '',
                  plan_summary TEXT NULL,
                  plan_price VARCHAR(80) NOT NULL DEFAULT '',
                  plan_itinerary JSON NULL,
                  plan_includes JSON NULL,
                  plan_tips TEXT NULL,
                  reviewed_at DATETIME NULL,
                  reviewed_by INT NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX ix_custom_travel_requests_request_no (request_no),
                  INDEX ix_custom_travel_requests_user_id (user_id),
                  INDEX ix_custom_travel_requests_user_no (user_no),
                  INDEX ix_custom_travel_requests_status (status),
                  INDEX ix_custom_travel_user_status (user_id, status),
                  INDEX ix_custom_travel_status_created (status, created_at)
                )
                """
            )
        )

        school_count = (
            await connection.execute(text("SELECT COUNT(*) FROM school_sites"))
        ).scalar() or 0
        if school_count == 0:
            await connection.execute(
                text(
                    """
                    INSERT INTO school_sites
                    (name, short_name, city, district, logo, status, is_current, review_status, display_weight, sort_order, description)
                    VALUES
                    ('水院-广东水利电力职业技术学院', '水院', '广州市', '从化区', '', 1, 1, 'approved', 50, 1, '当前合作入驻站点'),
                    ('广东食品药品职业学院', '', '广州市', '天河区', '', 1, 0, 'approved', 40, 2, '已入驻站点'),
                    ('厦门大学嘉庚学院', '', '厦门市', '龙海区', '', 1, 0, 'approved', 30, 3, '已入驻站点'),
                    ('广东行政职业学院', '', '广州市', '白云区', '', 1, 0, 'approved', 20, 4, '已入驻站点'),
                    ('广东职业技术学院', '', '佛山市', '高明区', '', 1, 0, 'approved', 10, 5, '已入驻站点')
                    """
                )
            )


async def create_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await run_schema_updates()
