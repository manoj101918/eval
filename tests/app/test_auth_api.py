from sqlalchemy import select

from backend.app.auth import SESSION_COOKIE
from backend.app.models import AuditLog
from tests.app.conftest import add_user, login, make_client


async def test_login_sets_httponly_cookie_and_me_works(app, client):
    await add_user(app, "T100", name="Asha")
    response = await client.post("/api/auth/login",
                                 json={"employee_id": "T100", "password": "pass-word-1"})
    assert response.status_code == 200
    assert response.json() == {"employee_id": "T100", "name": "Asha", "role": "teacher",
                               "must_change_password": False}
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    me = await client.get("/api/auth/me")
    assert me.json()["employee_id"] == "T100"


async def test_wrong_password_and_unknown_user_get_the_same_answer(app, client):
    await add_user(app, "T100")
    wrong = await client.post("/api/auth/login", json={"employee_id": "T100", "password": "x"})
    unknown = await client.post("/api/auth/login", json={"employee_id": "NOPE", "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid employee ID or password."}


async def test_lockout_after_repeated_failures(app, client):
    await add_user(app, "T100")
    for _ in range(3):  # login_max_failures=3 in test settings
        await client.post("/api/auth/login", json={"employee_id": "T100", "password": "bad"})
    locked = await client.post("/api/auth/login",
                               json={"employee_id": "T100", "password": "pass-word-1"})
    assert locked.status_code == 423


async def test_writes_need_csrf_header(app):
    await add_user(app, "T100")
    async with make_client(app) as c:
        c.headers.pop("X-Requested-With")
        response = await c.post("/api/auth/login",
                                json={"employee_id": "T100", "password": "pass-word-1"})
    assert response.status_code == 403


async def test_not_logged_in(client):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_ends_session(app, client):
    await add_user(app, "T100")
    await login(client, "T100")
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_first_login_must_change_password(app, client):
    await add_user(app, "T100", must_change=True)
    me = await login(client, "T100")
    assert me["must_change_password"] is True

    bad = await client.post("/api/auth/change-password",
                            json={"current_password": "wrong", "new_password": "a-new-pass"})
    assert bad.status_code == 400
    weak = await client.post("/api/auth/change-password",
                             json={"current_password": "pass-word-1", "new_password": "short"})
    assert weak.status_code == 400 and "at least 8" in weak.json()["detail"]

    ok = await client.post("/api/auth/change-password",
                           json={"current_password": "pass-word-1", "new_password": "a-new-pass"})
    assert ok.status_code == 204
    assert (await client.get("/api/auth/me")).json()["must_change_password"] is False
    await client.post("/api/auth/logout")
    await login(client, "T100", "a-new-pass")


async def test_password_change_signs_out_other_sessions(app):
    await add_user(app, "T100")
    async with make_client(app) as laptop, make_client(app) as phone:
        await login(laptop, "T100")
        await login(phone, "T100")
        await laptop.post("/api/auth/change-password",
                          json={"current_password": "pass-word-1", "new_password": "new-pass-9"})
        assert (await laptop.get("/api/auth/me")).status_code == 200
        assert (await phone.get("/api/auth/me")).status_code == 401


async def test_login_is_audited(app, client):
    user = await add_user(app, "T100")
    await login(client, "T100")
    async with app.state.sessionmaker() as db:
        entry = await db.scalar(select(AuditLog).where(AuditLog.action == "login"))
    assert entry.user_id == user.id
