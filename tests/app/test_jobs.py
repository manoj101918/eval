import logging

import groq
import pytest
from sqlalchemy import select

from backend.app.models import Script
from backend.app.queries import load_script
from backend.extract.vision import VisionClient
from backend.grading.grader import Grader
from tests.app.conftest import add_user, app_settings, login
from tests.app.factories import create_exam, script_pdf
from tests.fakes import TPD_MESSAGE, FakeGroq, api_error, message, page_number_of

ANSWER_TEXT = "Momentum is conserved"


def vision_handler(request):
    assert page_number_of(request) == 2  # the cover is read by QR, never sent
    return message({"segments": [
        {"question_number": "1", "text": "(c)", "has_diagram": False, "illegible": False,
         "illegible_notes": []},
        {"question_number": "5", "text": ANSWER_TEXT, "has_diagram": False,
         "illegible": False, "illegible_notes": []}]})


def grade(marks):
    return message({"marks_awarded": marks, "points_met": ["p1", "p2"], "points_missing": ["p3"],
                    "reason": "Two of three points.", "confidence": "high",
                    "ocr_problem": False})


BEHAVIOUR = {"vision": vision_handler, "grade": lambda r: grade(2)}


@pytest.fixture
def settings(tmp_path):
    return app_settings(tmp_path, transcribe_provider="groq", page_rotation="0",
                        vision_backoff_base_s=0, vision_backoff_max_s=0, vision_max_retries=0)


@pytest.fixture
def app_factories():
    return {
        "extraction_factory": lambda s: VisionClient(FakeGroq(lambda r: BEHAVIOUR["vision"](r)),
                                                     s),
        "grader_factory": lambda s: Grader(FakeGroq(lambda r: BEHAVIOUR["grade"](r)), s),
    }


@pytest.fixture(autouse=True)
def reset_behaviour():
    yield
    BEHAVIOUR.update(vision=vision_handler, grade=lambda r: grade(2))


async def upload_one(app, client, pdf=None, filename="21CS045 Asha.pdf"):
    await add_user(app, "ADM1", role="admin")
    await add_user(app, "T100")
    await login(client, "ADM1")
    exam = (await create_exam(client)).json()
    bundle = (await client.post(f"/api/admin/exams/{exam['id']}/bundles",
                                json={"code": "B1", "teacher_employee_id": "T100"})).json()
    response = await client.post(f"/api/admin/bundles/{bundle['id']}/scripts",
                                 files=[("files", (filename, pdf or script_pdf(),
                                                   "application/pdf"))])
    await app.state.runner.join()
    script_id = response.json()["accepted"][0]["id"]
    async with app.state.sessionmaker() as db:
        return await load_script(db, script_id)


async def test_uploaded_script_is_graded_in_background(app, client, settings):
    script = await upload_one(app, client)
    assert (script.status, script.error) == ("graded", None)
    assert (script.roll_number, script.roll_number_source) == ("21CS045", "qr")
    assert script.page_count == 2
    for page in (1, 2):
        assert (settings.storage_dir / "scripts" / str(script.id) / "pages"
                / f"page_{page}.jpg").read_bytes()[:2] == b"\xff\xd8"
    q = {m.question: m for m in script.questions}
    assert (q["1"].ai_marks, q["1"].final_marks, q["1"].qtype) == (1, 1, "mcq")
    assert (q["5"].ai_marks, q["5"].final_marks) == (2, 2)
    assert q["5"].points_missing == ["p3"]
    assert q["5"].confirmed is False
    assert script.extraction_json and script.grading_json
    detail = (await client.get(f"/api/admin/bundles/{script.bundle_id}")).json()
    assert detail["status"] == "ready"


async def test_unreadable_pdf_fails_cleanly(app, client):
    script = await upload_one(app, client, pdf=b"%PDF-1.4 garbage")
    assert (script.status, script.error) == ("failed", "unreadable_pdf")
    assert script.questions == []


async def test_quota_exhausted_marks_script_for_retry(app, client):
    BEHAVIOUR["grade"] = lambda r: api_error(groq.RateLimitError, 429, message=TPD_MESSAGE)
    script = await upload_one(app, client)
    assert (script.status, script.error) == ("failed", "quota")
    assert script.questions == []  # never half-graded


async def test_rejected_api_key(app, client):
    BEHAVIOUR["vision"] = lambda r: api_error(groq.AuthenticationError, 401)
    script = await upload_one(app, client)
    assert (script.status, script.error) == ("failed", "api_key")


async def test_retry_after_failure_grades_the_script(app, client):
    BEHAVIOUR["grade"] = lambda r: api_error(groq.RateLimitError, 429, message=TPD_MESSAGE)
    script = await upload_one(app, client)
    # Next day: the quota has reset. The runner's grader remembers it hit the daily limit
    # (as in production until restart), so give the runner fresh clients.
    BEHAVIOUR["grade"] = lambda r: grade(3)
    from worker.jobs import make_handler

    app.state.runner._handler = make_handler(app.state)
    await client.post(f"/api/admin/bundles/{script.bundle_id}/retry-failed")
    await app.state.runner.join()
    async with app.state.sessionmaker() as db:
        retried = await db.scalar(select(Script).where(Script.id == script.id))
    assert retried.status == "graded"


async def test_job_logs_hold_no_student_data(app, client, caplog):
    caplog.set_level(logging.DEBUG)
    await upload_one(app, client)
    for secret in ("21CS045", "Asha", ANSWER_TEXT, "Two of three points."):
        assert secret not in caplog.text
