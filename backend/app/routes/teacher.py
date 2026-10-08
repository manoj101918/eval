"""Teacher review: my bundles, scripts with page images and AI marks, edits, approval,
bundle submission. A teacher only ever sees bundles assigned to them; anything else is
reported as not found."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.audit import record
from backend.app.deps import AppSettings, DbSession, TeacherUser
from backend.app.exporting import run_export
from backend.app.marks import approval_problems, counted_ids, total, valid_marks
from backend.app.models import Bundle, Exam, Script, User
from backend.app.queries import load_bundle, load_exam_full, load_script
from backend.app.status import bundle_status, exam_status, script_counts
from backend.app.storage import page_image

router = APIRouter(prefix="/api/my", tags=["teacher"])


# --- schemas --------------------------------------------------------------------------


class ExamInfo(BaseModel):
    id: int
    name: str
    subject: str
    class_section: str
    max_marks: float


class MyBundle(BaseModel):
    id: int
    code: str
    exam: ExamInfo
    status: str
    total: int
    graded: int
    approved: int
    pending: int
    failed: int
    can_submit: bool


class ScriptItem(BaseModel):
    id: int
    roll_number: str | None
    status: str
    error: str | None
    ai_total: float | None
    final_total: float | None
    to_check: int  # flagged questions not yet confirmed


class BundleScripts(MyBundle):
    scripts: list[ScriptItem]


class QuestionOut(BaseModel):
    id: int
    question: str
    qtype: str
    max_marks: float
    choice_group: str | None
    counted: bool
    ai_status: str
    ai_marks: float | None
    ai_reason: str
    ai_confidence: str | None
    points_met: list[str]
    points_missing: list[str]
    needs_review: bool
    review_notes: list[str]
    pages: list[int]
    final_marks: float | None
    teacher_comment: str
    confirmed: bool
    changed: bool


class LeftoverText(BaseModel):
    """Text the system could not attach to a question (the teacher may find an answer)."""

    label: str | None
    pages: list[int]
    text: str


class ScriptOut(BaseModel):
    id: int
    bundle_id: int
    exam: ExamInfo
    status: str
    editable: bool
    roll_number: str | None
    roll_number_source: str | None
    page_count: int
    failed_pages: list[int]
    questions: list[QuestionOut]
    ai_total: float
    final_total: float
    max_marks: float
    leftovers: list[LeftoverText]
    problems: list[str]  # what still blocks approval
    previous_id: int | None
    next_id: int | None


class QuestionEdit(BaseModel):
    final_marks: float | None = None
    teacher_comment: str | None = Field(default=None, max_length=2000)
    confirmed: bool | None = None


class RollEdit(BaseModel):
    roll_number: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9/-]+$")


class SubmitResult(BaseModel):
    bundle: MyBundle
    exam_completed: bool
    exported: bool


# --- helpers --------------------------------------------------------------------------


def exam_info(exam: Exam) -> ExamInfo:
    return ExamInfo(id=exam.id, name=exam.name, subject=exam.subject,
                    class_section=exam.class_section, max_marks=exam.max_marks)


def my_bundle(bundle: Bundle) -> MyBundle:
    counts = script_counts(bundle)
    pending = counts["queued"] + counts["processing"]
    can_submit = (bundle.submitted_at is None and bool(bundle.scripts)
                  and counts["approved"] == len(bundle.scripts))
    return MyBundle(id=bundle.id, code=bundle.code, exam=exam_info(bundle.exam),
                    status=bundle_status(bundle), total=len(bundle.scripts),
                    graded=counts["graded"], approved=counts["approved"], pending=pending,
                    failed=counts["failed"], can_submit=can_submit)


def script_item(script: Script) -> ScriptItem:
    has_marks = script.status in ("graded", "approved")
    ids = counted_ids(script.questions)
    return ScriptItem(
        id=script.id, roll_number=script.roll_number, status=script.status, error=script.error,
        ai_total=total(script.questions, use_final=False) if has_marks else None,
        final_total=total(script.questions) if has_marks else None,
        to_check=sum(1 for q in script.questions
                     if q.needs_review and not q.confirmed and q.id in ids),
    )


def own_bundle(bundle: Bundle, user: User) -> Bundle:
    if bundle.teacher_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bundle not found.")
    return bundle


async def own_script(db, script_id: int, user: User) -> Script:
    script = await load_script(db, script_id)
    if script.bundle.teacher_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found.")
    return script


def require_editable(script: Script) -> None:
    if script.bundle.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bundle is already submitted.")
    if script.status == "approved":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This script is approved. Reopen it to make changes.")
    if script.status != "graded":
        raise HTTPException(status.HTTP_409_CONFLICT, "This script has not been graded yet.")


def leftovers(script: Script) -> tuple[list[LeftoverText], list[int]]:
    if not script.extraction_json:
        return [], []
    extraction = json.loads(script.extraction_json)
    grading = json.loads(script.grading_json or "{}")
    unmatched = {u["key"] for u in grading.get("unmatched_answers", [])}
    items = [LeftoverText(label=None, pages=[u["page"]], text=u["text"])
             for u in extraction.get("unassigned", [])]
    items += [LeftoverText(label=key, pages=a["pages"], text=a["text"])
              for key, a in extraction.get("answers", {}).items() if key in unmatched]
    return items, extraction.get("failed_pages", [])


def question_out(q, counted: set[int]) -> QuestionOut:
    return QuestionOut(
        id=q.id, question=q.question, qtype=q.qtype, max_marks=q.max_marks,
        choice_group=q.choice_group, counted=q.id in counted, ai_status=q.ai_status,
        ai_marks=q.ai_marks, ai_reason=q.ai_reason, ai_confidence=q.ai_confidence,
        points_met=q.points_met, points_missing=q.points_missing, needs_review=q.needs_review,
        review_notes=q.review_notes, pages=q.pages, final_marks=q.final_marks,
        teacher_comment=q.teacher_comment, confirmed=q.confirmed,
        changed=q.final_marks != q.ai_marks,
    )


def script_out(script: Script) -> ScriptOut:
    siblings = [s.id for s in script.bundle.scripts]
    i = siblings.index(script.id)
    counted = counted_ids(script.questions)
    extra, failed_pages = leftovers(script)
    has_marks = script.status in ("graded", "approved")
    return ScriptOut(
        id=script.id, bundle_id=script.bundle_id, exam=exam_info(script.bundle.exam),
        status=script.status,
        editable=script.status == "graded" and script.bundle.submitted_at is None,
        roll_number=script.roll_number, roll_number_source=script.roll_number_source,
        page_count=script.page_count, failed_pages=failed_pages,
        questions=[question_out(q, counted) for q in script.questions],
        ai_total=total(script.questions, use_final=False) if has_marks else 0.0,
        final_total=total(script.questions) if has_marks else 0.0,
        max_marks=script.bundle.exam.max_marks, leftovers=extra,
        problems=approval_problems(script.questions, script.roll_number)
        if script.status == "graded" else [],
        previous_id=siblings[i - 1] if i > 0 else None,
        next_id=siblings[i + 1] if i + 1 < len(siblings) else None,
    )


async def reload(db, script_id: int, user: User) -> ScriptOut:
    return script_out(await own_script(db, script_id, user))


# --- bundles --------------------------------------------------------------------------


@router.get("/bundles")
async def my_bundles(user: TeacherUser, db: DbSession) -> list[MyBundle]:
    bundles = await db.scalars(
        select(Bundle).where(Bundle.teacher_id == user.id).order_by(Bundle.id)
        .options(selectinload(Bundle.scripts), selectinload(Bundle.exam)))
    return [my_bundle(b) for b in bundles]


@router.get("/bundles/{bundle_id}")
async def bundle_scripts(bundle_id: int, user: TeacherUser, db: DbSession) -> BundleScripts:
    bundle = own_bundle(await load_bundle(db, bundle_id), user)
    scripts = list(await db.scalars(
        select(Script).where(Script.bundle_id == bundle.id).order_by(Script.id)
        .options(selectinload(Script.questions))))
    return BundleScripts(**my_bundle(bundle).model_dump(),
                         scripts=[script_item(s) for s in scripts])


@router.post("/bundles/{bundle_id}/submit")
async def submit_bundle(bundle_id: int, user: TeacherUser, db: DbSession,
                        settings: AppSettings) -> SubmitResult:
    bundle = own_bundle(await load_bundle(db, bundle_id), user)
    if bundle.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bundle is already submitted.")
    not_ready = [s for s in bundle.scripts if s.status != "approved"]
    if not bundle.scripts or not_ready:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"message": "Approve every script before submitting.",
                             "not_approved": [s.id for s in not_ready]})
    bundle.submitted_at = datetime.now(UTC)
    bundle.submitted_by = user.id
    await record(db, user.id, "submit_bundle", "bundle", bundle.id)
    await db.commit()

    exam = await load_exam_full(db, bundle.exam_id)
    completed = exam_status(exam) == "completed"
    exported = False
    if completed:  # last bundle of the exam: write the marks to Excel
        exported = not await run_export(db, exam.id, settings, user.id)
    return SubmitResult(bundle=my_bundle(await load_bundle(db, bundle.id)),
                        exam_completed=completed, exported=exported)


# --- scripts --------------------------------------------------------------------------


@router.get("/scripts/{script_id}")
async def get_script(script_id: int, user: TeacherUser, db: DbSession) -> ScriptOut:
    return script_out(await own_script(db, script_id, user))


@router.get("/scripts/{script_id}/pages/{page}")
async def get_page(script_id: int, page: int, user: TeacherUser, db: DbSession,
                   settings: AppSettings,
                   size: Literal["full", "thumb"] = "full") -> FileResponse:
    script = await own_script(db, script_id, user)
    path = page_image(settings.storage_dir, script.id, page)
    if size == "thumb":
        thumb = page_image(settings.storage_dir, script.id, page, thumb=True)
        if await asyncio.to_thread(thumb.is_file):  # older scripts have no thumbnails
            path = thumb
    if not 1 <= page <= script.page_count or not await asyncio.to_thread(path.is_file):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found.")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})


@router.patch("/scripts/{script_id}/questions/{question_id}")
async def edit_question(script_id: int, question_id: int, body: QuestionEdit,
                        user: TeacherUser, db: DbSession) -> ScriptOut:
    script = await own_script(db, script_id, user)
    require_editable(script)
    q = next((q for q in script.questions if q.id == question_id), None)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    old = {"final_marks": q.final_marks, "confirmed": q.confirmed}
    sent = body.model_fields_set
    if "final_marks" in sent:
        if body.final_marks is None or not valid_marks(body.final_marks, q.max_marks):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Marks must be between 0 and {q.max_marks:g}, in steps of 0.5.")
        q.final_marks = body.final_marks
        q.confirmed = True  # entering a mark means the teacher looked at it
    if "teacher_comment" in sent and body.teacher_comment is not None:
        q.teacher_comment = body.teacher_comment
    if "confirmed" in sent and body.confirmed is not None:
        q.confirmed = body.confirmed
    q.updated_at = datetime.now(UTC)
    q.updated_by = user.id
    await record(db, user.id, "edit_mark", "question_mark", q.id, script_id=script.id,
                 old=old, new={"final_marks": q.final_marks, "confirmed": q.confirmed})
    await db.commit()
    return await reload(db, script.id, user)


@router.patch("/scripts/{script_id}")
async def edit_roll_number(script_id: int, body: RollEdit, user: TeacherUser,
                           db: DbSession) -> ScriptOut:
    script = await own_script(db, script_id, user)
    require_editable(script)
    old = script.roll_number
    script.roll_number = body.roll_number.upper()
    script.roll_number_source = "teacher"
    await record(db, user.id, "edit_roll_number", "script", script.id, old=old,
                 new=script.roll_number)
    await db.commit()
    return await reload(db, script.id, user)


@router.post("/scripts/{script_id}/approve")
async def approve_script(script_id: int, user: TeacherUser, db: DbSession) -> ScriptOut:
    script = await own_script(db, script_id, user)
    require_editable(script)
    problems = approval_problems(script.questions, script.roll_number)
    if problems:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"message": "This script cannot be approved yet.",
                             "problems": problems})
    script.status = "approved"
    script.approved_at = datetime.now(UTC)
    script.approved_by = user.id
    await record(db, user.id, "approve_script", "script", script.id,
                 total=total(script.questions))
    await db.commit()
    return await reload(db, script.id, user)


@router.post("/scripts/{script_id}/reopen")
async def reopen_script(script_id: int, user: TeacherUser, db: DbSession) -> ScriptOut:
    script = await own_script(db, script_id, user)
    if script.bundle.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bundle is already submitted.")
    if script.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "This script is not approved.")
    script.status = "graded"
    script.approved_at = None
    script.approved_by = None
    await record(db, user.id, "reopen_script", "script", script.id)
    await db.commit()
    return await reload(db, script.id, user)
