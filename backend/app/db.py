"""Database engine and session factory (SQLite for development/tests, PostgreSQL in production)."""

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    db_url = make_url(url)
    if db_url.get_backend_name() == "sqlite":
        if db_url.database not in (None, "", ":memory:"):
            Path(db_url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(url)
    else:
        # PostgreSQL (Supabase). pre_ping drops connections the pooler has closed;
        # no statement cache, so Supabase's transaction pooler (port 6543) also works.
        engine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5,
                                     connect_args={"statement_cache_size": 0})
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys unless asked, per connection.
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
