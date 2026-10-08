from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.app.models import AuditLog, Bundle, Exam, Script
from tests.app.conftest import add_user, login, make_client
from tests.app.factories import create_exam, scheme_xlsx, script_pdf


@pytest.fixture
async def admin(app, client):
    await add_user(app, "ADM1", role="admin")
    await add_user(app, "T100", name="Asha")
    await add_user(app, "T200", name="Ravi")
    await login(client, "ADM1")
    return client


async def make_bundle(admin, code="B1", teacher="T100"):
    exam = (await create_exam(admin)).json()
    response = await admin.post(f"/api/admin/exams/{exam['id']}/bundles",
                                json={"code": code, "teacher_employee_id": teacher})
    assert response.status_code == 201, response.text
    return exam, response.json()


async def upload(admin, bundle_id, *files):
    return await admin.post(f"/api/admin/bundles/{bundle_id}/scripts",
                            files=[("files", f) for f in files])


# --- access ----------------------------------------------------------------------------


async def test_admin_only(app, client):
    assert (await client.get("/api/admin/users")).status_code == 401
    await add_user(app, "T100")
    await login(client, "T100")
    assert (await client.get("/api/admin/users")).status_code == 403


async def test_must_change_password_blocks_admin_api(app, client):
    await add_user(app, "ADM1", role="admin", must_change=True)
    await login(client, "ADM1")
    response = await client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.json()["detail"] == "password_change_required"


# --- users -----------------------------------------------------------------------------


async def test_create_user_with_temporary_password(app, admin):
    response = await admin.post("/api/admin/users", json={"employee_id": "T300", "name": "Meena"})
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["must_change_password"] is True
    async with make_client(app) as teacher:
        me = await login(teacher, "T300", body["temporary_password"])
        assert me["must_change_password"] is True
    dup = await admin.post("/api/admin/users", json={"employee_id": "T300", "name": "x"})
    assert dup.status_code == 409
    bad = await admin.post("/api/admin/users", json={"employee_id": "T 300!", "name": "x"})
    assert bad.status_code == 422


async def test_reset_password_unlocks_and_signs_out(app, admin):
    async with make_client(app) as teacher:
        await login(teacher, "T100")
        users = {u["employee_id"]: u for u in (await admin.get("/api/admin/users")).json()}
        reset = await admin.post(f"/api/admin/users/{users['T100']['id']}/reset-password")
        assert reset.status_code == 200
        assert (await teacher.get("/api/auth/me")).status_code == 401
        await login(teacher, "T100", reset.json()["temporary_password"])


async def test_deactivate(app, admin):
    users = {u["employee_id"]: u for u in (await admin.get("/api/admin/users")).json()}
    off = await admin.post(f"/api/admin/users/{users['T100']['id']}/active?active=false")
    assert off.json()["active"] is False
    async with make_client(app) as teacher:
        r = await teacher.post("/api/auth/login",
                               json={"employee_id": "T100", "password": "pass-word-1"})
        assert r.status_code == 401
    me = await admin.post(f"/api/admin/users/{users['ADM1']['id']}/active?active=false")
    assert me.status_code == 400


# --- exams -----------------------------------------------------------------------------


async def test_create_exam_validates_scheme(app, admin, settings):
    response = await create_exam(admin)
    assert response.status_code == 201, response.text
    exam = response.json()
    assert (exam["max_marks"], exam["status"]) == (4, "draft")
    assert [q["question"] for q in exam["questions"]] == ["1", "5"]
    assert (settings.storage_dir / "exams" / str(exam["id"]) / "scheme.xlsx").exists()


async def test_bad_scheme_lists_problems(admin):
    response = await create_exam(admin, scheme_xlsx([["5", "lots", "long", None, "x"]]))
    assert response.status_code == 422
    assert response.json()["detail"]["problems"] == ["row 2 (5): Max marks must be a number"]


async def test_scheme_must_be_xlsx(admin):
    response = await admin.post(
        "/api/admin/exams", data={"name": "M", "subject": "P", "class_section": "A"},
        files={"scheme": ("scheme.xlsx", b"not a workbook", "application/octet-stream")})
    assert response.status_code == 400


# --- bundles and uploads ---------------------------------------------------------------


async def test_create_bundle_and_assign(admin):
    exam, bundle = await make_bundle(admin)
    assert bundle["teacher"]["employee_id"] == "T100"
    assert bundle["status"] == "open"
    dup = await admin.post(f"/api/admin/exams/{exam['id']}/bundles", json={"code": "B1"})
    assert dup.status_code == 409
    unknown = await admin.post(f"/api/admin/exams/{exam['id']}/bundles",
                               json={"code": "B2", "teacher_employee_id": "NOPE"})
    assert unknown.status_code == 400
    not_teacher = await admin.post(f"/api/admin/exams/{exam['id']}/bundles",
                                   json={"code": "B3", "teacher_employee_id": "ADM1"})
    assert not_teacher.status_code == 400
    moved = await admin.put(f"/api/admin/bundles/{bundle['id']}/teacher",
                            json={"teacher_employee_id": "T200"})
    assert moved.json()["teacher"]["employee_id"] == "T200"


async def test_upload_scripts_queues_jobs(app, admin, settings):
    _, bundle = await make_bundle(admin)
    response = await upload(admin, bundle["id"],
                            ("21CS045 Asha.pdf", script_pdf(), "application/pdf"),
                            ("notes.txt", b"hello", "text/plain"))
    assert response.status_code == 200
    body = response.json()
    assert [s["filename"] for s in body["accepted"]] == ["21CS045 Asha.pdf"]
    assert body["rejected"] == [{"filename": "notes.txt", "reason": "wrong file type"}]
    script_id = body["accepted"][0]["id"]
    assert app.state.jobs_seen == [script_id]
    stored = settings.storage_dir / "scripts" / str(script_id) / "original.pdf"
    assert stored.read_bytes().startswith(b"%PDF-")
    assert "Asha" not in str(stored)  # file names never become paths
    detail = (await admin.get(f"/api/admin/bundles/{bundle['id']}")).json()
    assert detail["status"] == "grading" and detail["counts"]["queued"] == 1


async def test_upload_size_limit(app, admin, settings):
    settings.max_upload_mb = 1
    _, bundle = await make_bundle(admin)
    big = b"%PDF-" + b"0" * (1024 * 1024 + 10)
    body = (await upload(admin, bundle["id"], ("big.pdf", big, "application/pdf"))).json()
    assert body["accepted"] == [] and "larger than 1 MB" in body["rejected"][0]["reason"]


async def set_submitted(app, bundle_id):
    async with app.state.sessionmaker() as db:
        bundle = await db.get(Bundle, bundle_id)
        bundle.submitted_at = datetime.now(UTC)
        await db.commit()


async def test_submitted_bundle_is_locked_until_reopened(app, admin):
    _, bundle = await make_bundle(admin)
    await set_submitted(app, bundle["id"])
    assert (await upload(admin, bundle["id"], ("a.pdf", script_pdf(), "application/pdf"))
            ).status_code == 409
    assert (await admin.put(f"/api/admin/bundles/{bundle['id']}/teacher",
                            json={"teacher_employee_id": "T200"})).status_code == 409
    no_reason = await admin.post(f"/api/admin/bundles/{bundle['id']}/reopen", json={})
    assert no_reason.status_code == 422
    reopened = await admin.post(f"/api/admin/bundles/{bundle['id']}/reopen",
                                json={"reason": "teacher asked to fix Q5"})
    assert reopened.json()["status"] == "open"
    async with app.state.sessionmaker() as db:
        entry = await db.scalar(select(AuditLog).where(AuditLog.action == "reopen_bundle"))
    assert entry.detail == {"reason": "teacher asked to fix Q5"}


async def test_reopen_invalidates_export(app, admin):
    exam, bundle = await make_bundle(admin)
    await set_submitted(app, bundle["id"])
    async with app.state.sessionmaker() as db:
        e = await db.get(Exam, exam["id"])
        e.exported_at = datetime.now(UTC)
        await db.commit()
    await admin.post(f"/api/admin/bundles/{bundle['id']}/reopen", json={"reason": "fix marks"})
    detail = (await admin.get(f"/api/admin/exams/{exam['id']}")).json()
    assert detail["exported_at"] is None
    assert "export again" in detail["export_error"]


async def test_retry_failed(app, admin):
    _, bundle = await make_bundle(admin)
    sid = (await upload(admin, bundle["id"], ("a.pdf", script_pdf(), "application/pdf"))
           ).json()["accepted"][0]["id"]
    async with app.state.sessionmaker() as db:
        s = await db.get(Script, sid)
        s.status, s.error = "failed", "quota"
        await db.commit()
    app.state.jobs_seen.clear()
    summary = (await admin.post(f"/api/admin/bundles/{bundle['id']}/retry-failed")).json()
    assert summary["counts"]["queued"] == 1
    assert app.state.jobs_seen == [sid]


async def test_exam_list_and_progress(admin):
    exam, bundle = await make_bundle(admin)
    await upload(admin, bundle["id"], ("a.pdf", script_pdf(), "application/pdf"))
    exams = (await admin.get("/api/admin/exams")).json()
    assert exams[0] | {} == exams[0]
    assert (exams[0]["id"], exams[0]["status"], exams[0]["scripts"]) == (exam["id"], "grading", 1)


async def test_unfinished_scripts_requeued_on_restart(tmp_path, settings):
    from backend.app.main import create_app
    from tests.app.conftest import recording_jobs

    first = create_app(settings, job_handler_factory=recording_jobs)
    async with first.router.lifespan_context(first):
        async with first.state.sessionmaker() as db:
            exam = Exam(name="M", subject="P", class_section="A", scheme_json="{}",
                        scheme_filename="s", max_marks=1)
            bundle = Bundle(code="B", exam=exam)
            db.add_all([Script(bundle=bundle, original_filename="a", pdf_path="p",
                               status="processing"),
                        Script(bundle=bundle, original_filename="b", pdf_path="p",
                               status="graded")])
            await db.commit()
    second = create_app(settings, job_handler_factory=recording_jobs)
    async with second.router.lifespan_context(second):
        await second.state.runner.join()
        assert len(second.state.jobs_seen) == 1  # the interrupted one, not the graded one
