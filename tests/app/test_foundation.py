from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

from alembic import command
from backend.app import auth
from backend.app.manage import create_admin, init_db
from backend.app.models import AuthSession, Bundle, Exam, Script, User
from backend.app.status import bundle_status, exam_status

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def test_migrations_upgrade_and_downgrade(tmp_path):
    db = (tmp_path / "m.db").as_posix()
    cfg = alembic_config(f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"users", "sessions", "exams", "bundles", "scripts", "question_marks",
            "audit_log"} <= tables
    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(f"sqlite:///{db}")).get_table_names()) <= {"alembic_version"}


async def test_create_admin_once(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'a.db').as_posix()}"
    await init_db(url)
    password = await create_admin(url, "EMP001", "Exam Cell")
    assert password and len(password) == 12
    assert await create_admin(url, "EMP001", "Again") is None


# --- passwords, lockout, sessions -----------------------------------------------------


def test_password_hashing_and_rules():
    h = auth.hash_password("correct horse")
    assert auth.verify_password(h, "correct horse")
    assert not auth.verify_password(h, "wrong")
    assert not auth.verify_password("not-a-hash", "x")
    assert auth.password_problem("short", "EMP1")
    assert auth.password_problem("emp12345", "EMP12345")
    assert auth.password_problem("a-good-password", "EMP1") is None


async def add_user(factory, employee_id="T100", password="teacher-pass", **kw):
    async with factory() as db:
        user = User(employee_id=employee_id, name="Teacher", role="teacher",
                    password_hash=auth.hash_password(password), **kw)
        db.add(user)
        await db.commit()
        return user


async def test_lockout_after_failures(db_factory):
    await add_user(db_factory)
    async with db_factory() as db:
        for _ in range(3):
            assert (await auth.authenticate(db, "T100", "nope", max_failures=3,
                                            lock_minutes=15)) == (None, "invalid")
        user, reason = await auth.authenticate(db, "T100", "teacher-pass", max_failures=3,
                                               lock_minutes=15)
        assert (user, reason) == (None, "locked")


async def test_unknown_and_inactive_users_look_the_same(db_factory):
    await add_user(db_factory, active=False)
    async with db_factory() as db:
        assert (await auth.authenticate(db, "NOPE", "x", max_failures=5, lock_minutes=1))[1] \
            == "invalid"
        assert (await auth.authenticate(db, "T100", "teacher-pass", max_failures=5,
                                        lock_minutes=1))[1] == "invalid"


async def test_successful_login_resets_failures(db_factory):
    await add_user(db_factory)
    async with db_factory() as db:
        await auth.authenticate(db, "T100", "nope", max_failures=5, lock_minutes=1)
        user, _ = await auth.authenticate(db, "T100", "teacher-pass", max_failures=5,
                                          lock_minutes=1)
        assert user is not None and user.failed_logins == 0


async def test_sessions_expire_and_end(db_factory):
    user = await add_user(db_factory)
    async with db_factory() as db:
        user = await db.get(User, user.id)
        token = await auth.create_session(db, user, ttl_hours=1)
        assert (await auth.user_for_token(db, token)).employee_id == "T100"
        assert await auth.user_for_token(db, "forged") is None
        session = await db.scalar(select(AuthSession))
        assert session.token_hash != token  # only the hash is stored
        session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
        assert await auth.user_for_token(db, token) is None
        token2 = await auth.create_session(db, user, ttl_hours=1)
        await auth.end_session(db, token2)
        assert await auth.user_for_token(db, token2) is None


# --- derived status ---------------------------------------------------------------------


def bundle(*statuses, submitted=False):
    b = Bundle(code="B1", scripts=[Script(original_filename="f", pdf_path="p", status=s)
                                   for s in statuses])
    b.submitted_at = datetime.now(UTC) if submitted else None
    return b


@pytest.mark.parametrize(
    ("statuses", "submitted", "expected"),
    [
        ((), False, "open"),
        (("queued", "graded"), False, "grading"),
        (("processing",), False, "grading"),
        (("graded", "failed"), False, "ready"),
        (("graded", "approved"), False, "in_review"),
        (("approved",), True, "submitted"),
    ],
)
def test_bundle_status(statuses, submitted, expected):
    assert bundle_status(bundle(*statuses, submitted=submitted)) == expected


@pytest.mark.parametrize(
    ("bundles", "exported", "expected"),
    [
        ([], False, "draft"),
        ([bundle()], False, "draft"),
        ([bundle("queued"), bundle("approved", submitted=True)], False, "grading"),
        ([bundle("graded"), bundle("approved", submitted=True)], False, "in_review"),
        ([bundle("approved", submitted=True)] * 2, False, "completed"),
        ([bundle("approved", submitted=True)], True, "exported"),
    ],
)
def test_exam_status(bundles, exported, expected):
    exam = Exam(name="Mid 1", subject="Physics", class_section="XII-A", scheme_json="{}",
                scheme_filename="s.xlsx", max_marks=10, bundles=list(bundles))
    exam.exported_at = datetime.now(UTC) if exported else None
    assert exam_status(exam) == expected
