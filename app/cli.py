from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import or_, select

from app.core.auth import hash_password
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.migrations import ensure_database_current
from app.models import UserAccount
from app.seed import seed_data


async def bootstrap_admin(phone: str, password: str, nickname: str) -> None:
    await ensure_database_current()
    async with SessionLocal() as db:
        existing = await db.scalar(
            select(UserAccount).where(
                or_(UserAccount.phone == phone, UserAccount.user_no == "ADMIN001")
            )
        )
        if existing:
            raise RuntimeError("An administrator with this phone or user number already exists")
        db.add(
            UserAccount(
                user_no="ADMIN001",
                phone=phone,
                nickname=nickname,
                password_hash=hash_password(password),
                role="admin",
                status="active",
                is_registered=True,
            )
        )
        await db.commit()


def _password(value: str | None) -> str:
    password = value or getpass.getpass("Password: ")
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    return password


async def run() -> None:
    parser = argparse.ArgumentParser(description="Xuetuxing administrative commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    admin_parser = subparsers.add_parser("bootstrap-admin", help="Create the first administrator")
    admin_parser.add_argument("--phone", required=True)
    admin_parser.add_argument("--password")
    admin_parser.add_argument("--nickname", default="学徒行管理员")

    demo_parser = subparsers.add_parser("seed-development", help="Create development-only sample data")
    demo_parser.add_argument("--admin-phone", default="13800000000")
    demo_parser.add_argument("--admin-password")
    demo_parser.add_argument("--admin-nickname", default="学徒行管理员")

    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        await bootstrap_admin(args.phone, _password(args.password), args.nickname)
        print("Administrator created")
        return

    if settings.environment.lower() in {"production", "prod"}:
        raise RuntimeError("Development seed data is disabled in production")
    await ensure_database_current()
    await seed_data(args.admin_phone, _password(args.admin_password), args.admin_nickname)
    print("Development data created")


if __name__ == "__main__":
    asyncio.run(run())
