import pytest

from backend.app.db import make_engine
from backend.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
         "postgresql+asyncpg://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite+aiosqlite:///./data/app.db", "sqlite+aiosqlite:///./data/app.db"),
    ],
)
def test_supabase_url_uses_async_driver(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected


async def test_postgres_engine_options():
    engine = make_engine("postgresql+asyncpg://u:p@localhost:5432/db")  # no connection made
    try:
        assert engine.pool._pre_ping is True
        assert engine.dialect.driver == "asyncpg"
    finally:
        await engine.dispose()
