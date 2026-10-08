import httpx
import pytest

from backend.app import auth
from backend.app.db import Base, make_engine, make_sessionmaker
from backend.app.main import create_app
from backend.app.models import User
from backend.config import Settings

CSRF = {auth.CSRF_HEADER: auth.CSRF_VALUE}


@pytest.fixture
async def db_factory(tmp_path):
    """A fresh SQLite database with all tables; yields an async session factory."""
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


def app_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        export_dir=tmp_path / "exports",
        login_max_failures=3,
    )
    return Settings(**(base | overrides))


@pytest.fixture
def settings(tmp_path):
    return app_settings(tmp_path)


@pytest.fixture
async def app(settings, request):
    factories = getattr(request, "param", {}) or {}
    application = create_app(settings, **factories)
    async with application.router.lifespan_context(application):
        yield application


def make_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers=CSRF)


@pytest.fixture
async def client(app):
    async with make_client(app) as c:
        yield c


async def add_user(app, employee_id: str, role: str = "teacher", password: str = "pass-word-1",
                   must_change: bool = False, name: str = "Test User") -> User:
    async with app.state.sessionmaker() as db:
        user = User(employee_id=employee_id, name=name, role=role,
                    password_hash=auth.hash_password(password), must_change_password=must_change)
        db.add(user)
        await db.commit()
        return user


async def login(client: httpx.AsyncClient, employee_id: str, password: str = "pass-word-1"):
    response = await client.post("/api/auth/login",
                                 json={"employee_id": employee_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()
