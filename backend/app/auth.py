"""Passwords (argon2), session tokens and login lockout."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import AuthSession, User

SESSION_COOKIE = "aise_session"
CSRF_HEADER = "X-Requested-With"  # browsers cannot set custom headers cross-site without CORS
CSRF_VALUE = "aise"
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def temporary_password() -> str:
    """Readable one-time password handed to a teacher by the admin."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def password_problem(password: str, employee_id: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.lower() == employee_id.lower():
        return "Password must not be the employee ID."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def authenticate(
    db: AsyncSession, employee_id: str, password: str, *, max_failures: int, lock_minutes: int
) -> tuple[User | None, str | None]:
    """(user, None) on success, else (None, reason). Reasons are generic so they do not
    reveal whether an employee ID exists."""
    user = await db.scalar(select(User).where(User.employee_id == employee_id.strip()))
    now = datetime.now(UTC)
    if user is None:
        verify_password(_DUMMY_HASH, password)  # same work either way
        return None, "invalid"
    locked = as_utc(user.locked_until)
    if locked and locked > now:
        return None, "locked"
    if not user.active or not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= max_failures:
            user.locked_until = now + timedelta(minutes=lock_minutes)
            user.failed_logins = 0
        await db.commit()
        return None, "invalid"
    user.failed_logins = 0
    user.locked_until = None
    await db.commit()
    return user, None


async def create_session(db: AsyncSession, user: User, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id,
                       expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours)))
    await db.commit()
    return token


async def user_for_token(db: AsyncSession, token: str) -> User | None:
    session = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if session is None:
        return None
    if as_utc(session.expires_at) <= datetime.now(UTC) or not session.user.active:
        await db.delete(session)
        await db.commit()
        return None
    return session.user


async def end_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    await db.commit()


async def end_all_sessions(db: AsyncSession, user_id: int) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
