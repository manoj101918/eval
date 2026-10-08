import pytest

from backend.app.db import Base, make_engine, make_sessionmaker


@pytest.fixture
async def db_factory(tmp_path):
    """A fresh SQLite database with all tables; yields an async session factory."""
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()
