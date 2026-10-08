"""Request dependencies: database session, current user, role checks."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app import auth
from backend.app.models import User
from backend.config import Settings

# Paths a user who must change their password may still use.
PASSWORD_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(auth.SESSION_COOKIE)
    user = await auth.user_for_token(db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


async def require_teacher(user: CurrentUser) -> User:
    if user.role != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Teachers only.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
TeacherUser = Annotated[User, Depends(require_teacher)]
