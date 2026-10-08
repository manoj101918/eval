"""Setup commands.

    python -m backend.app.manage init-db
    python -m backend.app.manage create-admin <employee_id> --name "Exam Cell"
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from backend.app.auth import hash_password, temporary_password
from backend.app.db import Base, make_engine, make_sessionmaker
from backend.app.models import User
from backend.config import get_settings


async def init_db(url: str) -> None:
    """Create tables directly (development). Production uses `alembic upgrade head`."""
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def create_admin(url: str, employee_id: str, name: str) -> str | None:
    """Create an admin and return their one-time password (None if the ID exists)."""
    engine = make_engine(url)
    try:
        async with make_sessionmaker(engine)() as db:
            if await db.scalar(select(User).where(User.employee_id == employee_id)):
                return None
            password = temporary_password()
            db.add(User(employee_id=employee_id, name=name, role="admin",
                        password_hash=hash_password(password), must_change_password=True))
            await db.commit()
            return password
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create database tables (development)")
    admin = sub.add_parser("create-admin", help="create an admin account")
    admin.add_argument("employee_id")
    admin.add_argument("--name", default="Administrator")
    args = parser.parse_args(argv)
    url = get_settings().database_url

    if args.command == "init-db":
        asyncio.run(init_db(url))
        print("Database tables are ready.")
        return 0
    asyncio.run(init_db(url))
    password = asyncio.run(create_admin(url, args.employee_id, args.name))
    if password is None:
        print(f"error: employee ID {args.employee_id} already exists", file=sys.stderr)
        return 1
    print(f"Admin {args.employee_id} created. One-time password: {password}")
    print("It must be changed at first login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
