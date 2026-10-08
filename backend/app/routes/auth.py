"""Login, logout, current user, password change."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.app import auth
from backend.app.audit import record
from backend.app.deps import AppSettings, CurrentUser, DbSession
from backend.app.models import User
from backend.config import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class Me(BaseModel):
    employee_id: str
    name: str
    role: str
    must_change_password: bool


def me_of(user: User) -> Me:
    return Me(employee_id=user.employee_id, name=user.name, role=user.role,
              must_change_password=user.must_change_password)


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=settings.session_ttl_hours * 3600,
                        path="/")


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: DbSession,
                settings: AppSettings) -> Me:
    user, reason = await auth.authenticate(
        db, body.employee_id, body.password,
        max_failures=settings.login_max_failures, lock_minutes=settings.login_lock_minutes)
    if user is None:
        if reason == "locked":
            raise HTTPException(status.HTTP_423_LOCKED,
                                "Too many failed attempts. Try again later.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid employee ID or password.")
    _set_cookie(response, await auth.create_session(db, user, settings.session_ttl_hours),
                settings)
    await record(db, user.id, "login", "user", user.id)
    await db.commit()
    return me_of(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        await auth.end_session(db, token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> Me:
    return me_of(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChange, response: Response, user: CurrentUser,
                          db: DbSession, settings: AppSettings) -> None:
    user = await db.get(User, user.id)
    if not auth.verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong.")
    problem = auth.password_problem(body.new_password, user.employee_id)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user.password_hash = auth.hash_password(body.new_password)
    user.must_change_password = False
    await auth.end_all_sessions(db, user.id)  # sign out everywhere else
    await record(db, user.id, "change_password", "user", user.id)
    await db.commit()
    _set_cookie(response, await auth.create_session(db, user, settings.session_ttl_hours),
                settings)
