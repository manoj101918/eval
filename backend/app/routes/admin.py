"""Admin (exam cell): teacher accounts, exams, bundles, script uploads."""

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app import auth
from backend.app.audit import record
from backend.app.deps import AdminUser, AppSettings, DbSession
from backend.app.exporting import run_export
from backend.app.models import Bundle, Exam, Script, User
from backend.app.queries import load_bundle, load_exam
from backend.app.status import bundle_status, exam_status, script_counts
from backend.app.storage import (
    PDF_MAGIC,
    XLSX_MAGIC,
    UploadError,
    copy_file,
    exam_scheme,
    save_upload,
    script_pdf,
)
from backend.grading.scheme import MarkingScheme, SchemeError, load_scheme

router = APIRouter(prefix="/api/admin", tags=["admin"])

SCRIPT_STATES = ("queued", "processing", "graded", "failed", "approved")


# --- schemas --------------------------------------------------------------------------


class UserOut(BaseModel):
    id: int
    employee_id: str
    name: str
    role: str
    active: bool
    must_change_password: bool


class NewUser(BaseModel):
    employee_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    role: Literal["teacher", "admin"] = "teacher"


class NewUserOut(BaseModel):
    user: UserOut
    temporary_password: str


class TempPassword(BaseModel):
    temporary_password: str


class TeacherRef(BaseModel):
    employee_id: str
    name: str


class BundleSummary(BaseModel):
    id: int
    code: str
    teacher: TeacherRef | None
    status: str
    counts: dict[str, int]
    total: int


class QuestionInfo(BaseModel):
    question: str
    max_marks: float
    qtype: str
    choice_group: str | None


class ExamSummary(BaseModel):
    id: int
    name: str
    subject: str
    class_section: str
    max_marks: float
    status: str
    bundles: int
    scripts: int


class ExamDetail(ExamSummary):
    questions: list[QuestionInfo]
    bundle_list: list[BundleSummary]
    exported_at: str | None
    export_error: str | None


class NewBundle(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    teacher_employee_id: str | None = None


class AssignTeacher(BaseModel):
    teacher_employee_id: str


class ScriptRow(BaseModel):
    id: int
    filename: str
    status: str
    error: str | None
    roll_number: str | None


class BundleDetail(BundleSummary):
    exam_id: int
    scripts: list[ScriptRow]


class UploadResult(BaseModel):
    accepted: list[ScriptRow]
    rejected: list[dict[str, str]]


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


# --- helpers --------------------------------------------------------------------------


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, employee_id=u.employee_id, name=u.name, role=u.role,
                   active=u.active, must_change_password=u.must_change_password)


def bundle_summary(b: Bundle) -> BundleSummary:
    counts = script_counts(b)
    return BundleSummary(
        id=b.id, code=b.code, status=bundle_status(b), total=len(b.scripts),
        teacher=TeacherRef(employee_id=b.teacher.employee_id, name=b.teacher.name)
        if b.teacher else None,
        counts={s: counts.get(s, 0) for s in SCRIPT_STATES},
    )


def exam_summary(e: Exam) -> ExamSummary:
    return ExamSummary(id=e.id, name=e.name, subject=e.subject, class_section=e.class_section,
                       max_marks=e.max_marks, status=exam_status(e), bundles=len(e.bundles),
                       scripts=sum(len(b.scripts) for b in e.bundles))


def script_row(s: Script) -> ScriptRow:
    return ScriptRow(id=s.id, filename=s.original_filename, status=s.status, error=s.error,
                     roll_number=s.roll_number)


async def get_user(db, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return user


async def find_teacher(db, employee_id: str) -> User:
    teacher = await db.scalar(select(User).where(User.employee_id == employee_id))
    if teacher is None or teacher.role != "teacher" or not teacher.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"No active teacher with employee ID {employee_id}.")
    return teacher


def not_submitted(bundle: Bundle) -> None:
    if bundle.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This bundle is submitted. Reopen it first.")


# --- users ----------------------------------------------------------------------------


@router.get("/users")
async def list_users(_: AdminUser, db: DbSession) -> list[UserOut]:
    users = await db.scalars(select(User).order_by(User.role, User.employee_id))
    return [user_out(u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(body: NewUser, admin: AdminUser, db: DbSession) -> NewUserOut:
    if await db.scalar(select(User).where(User.employee_id == body.employee_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This employee ID already exists.")
    password = auth.temporary_password()
    user = User(employee_id=body.employee_id, name=body.name, role=body.role,
                password_hash=auth.hash_password(password), must_change_password=True)
    db.add(user)
    await db.flush()
    await record(db, admin.id, "create_user", "user", user.id, role=body.role)
    await db.commit()
    return NewUserOut(user=user_out(user), temporary_password=password)


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: int, admin: AdminUser, db: DbSession) -> TempPassword:
    user = await get_user(db, user_id)
    password = auth.temporary_password()
    user.password_hash = auth.hash_password(password)
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    await auth.end_all_sessions(db, user.id)
    await record(db, admin.id, "reset_password", "user", user.id)
    await db.commit()
    return TempPassword(temporary_password=password)


@router.post("/users/{user_id}/active")
async def set_active(user_id: int, active: bool, admin: AdminUser, db: DbSession) -> UserOut:
    user = await get_user(db, user_id)
    if user.id == admin.id and not active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate yourself.")
    user.active = active
    if not active:
        await auth.end_all_sessions(db, user.id)
    await record(db, admin.id, "activate" if active else "deactivate", "user", user.id)
    await db.commit()
    return user_out(user)


# --- exams ----------------------------------------------------------------------------


@router.post("/exams", status_code=status.HTTP_201_CREATED)
async def create_exam(
    admin: AdminUser, db: DbSession, settings: AppSettings,
    name: Annotated[str, Form(min_length=1, max_length=120)],
    subject: Annotated[str, Form(min_length=1, max_length=120)],
    class_section: Annotated[str, Form(min_length=1, max_length=60)],
    scheme: Annotated[UploadFile, File(description="Marking scheme .xlsx")],
) -> ExamDetail:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scheme.xlsx"
        try:
            await save_upload(scheme, path, max_bytes=5 * 1024 * 1024, magic=XLSX_MAGIC)
        except UploadError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Marking scheme file rejected: {exc}.") from None
        try:
            parsed = await asyncio.to_thread(load_scheme, path)
        except SchemeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"message": "The marking scheme has problems.", "problems": exc.problems},
            ) from None
        exam = Exam(name=name, subject=subject, class_section=class_section,
                    scheme_json=parsed.model_dump_json(),
                    scheme_filename=scheme.filename or "scheme.xlsx",
                    max_marks=parsed.total_marks, created_by=admin.id)
        db.add(exam)
        await db.flush()
        await copy_file(path, exam_scheme(settings.storage_dir, exam.id))
    await record(db, admin.id, "create_exam", "exam", exam.id)
    await db.commit()
    return await exam_detail(exam.id, admin, db)


@router.get("/exams")
async def list_exams(_: AdminUser, db: DbSession) -> list[ExamSummary]:
    exams = await db.scalars(select(Exam).order_by(Exam.id.desc()).options(
        selectinload(Exam.bundles).selectinload(Bundle.scripts)))
    return [exam_summary(e) for e in exams]


@router.get("/exams/{exam_id}")
async def exam_detail(exam_id: int, _: AdminUser, db: DbSession) -> ExamDetail:
    exam = await load_exam(db, exam_id)
    scheme = MarkingScheme.model_validate_json(exam.scheme_json)
    return ExamDetail(
        **exam_summary(exam).model_dump(),
        questions=[QuestionInfo(question=i.label, max_marks=i.max_marks, qtype=i.qtype,
                                choice_group=i.choice_group) for i in scheme.items],
        bundle_list=[bundle_summary(b) for b in exam.bundles],
        exported_at=exam.exported_at.isoformat() if exam.exported_at else None,
        export_error=exam.export_error,
    )


# --- bundles --------------------------------------------------------------------------


@router.post("/exams/{exam_id}/bundles", status_code=status.HTTP_201_CREATED)
async def create_bundle(exam_id: int, body: NewBundle, admin: AdminUser,
                        db: DbSession) -> BundleSummary:
    exam = await load_exam(db, exam_id)
    if any(b.code == body.code for b in exam.bundles):
        raise HTTPException(status.HTTP_409_CONFLICT, "A bundle with this code exists.")
    teacher = await find_teacher(db, body.teacher_employee_id) \
        if body.teacher_employee_id else None
    bundle = Bundle(exam_id=exam.id, code=body.code, teacher_id=teacher.id if teacher else None)
    db.add(bundle)
    await db.flush()
    await record(db, admin.id, "create_bundle", "bundle", bundle.id,
                 teacher_id=teacher.id if teacher else None)
    await db.commit()
    return bundle_summary(await load_bundle(db, bundle.id))


@router.get("/bundles/{bundle_id}")
async def bundle_detail(bundle_id: int, _: AdminUser, db: DbSession) -> BundleDetail:
    bundle = await load_bundle(db, bundle_id)
    return BundleDetail(**bundle_summary(bundle).model_dump(), exam_id=bundle.exam_id,
                        scripts=[script_row(s) for s in bundle.scripts])


@router.put("/bundles/{bundle_id}/teacher")
async def assign_teacher(bundle_id: int, body: AssignTeacher, admin: AdminUser,
                         db: DbSession) -> BundleSummary:
    bundle = await load_bundle(db, bundle_id)
    not_submitted(bundle)
    teacher = await find_teacher(db, body.teacher_employee_id)
    old = bundle.teacher_id
    bundle.teacher = teacher  # set the relationship, not just the id, so it is not stale
    await record(db, admin.id, "assign_teacher", "bundle", bundle.id, old=old, new=teacher.id)
    await db.commit()
    return bundle_summary(await load_bundle(db, bundle.id))


@router.post("/bundles/{bundle_id}/scripts")
async def upload_scripts(
    bundle_id: int, request: Request, admin: AdminUser, db: DbSession, settings: AppSettings,
    files: Annotated[list[UploadFile], File(description="One PDF per student")],
) -> UploadResult:
    bundle = await load_bundle(db, bundle_id)
    not_submitted(bundle)
    accepted: list[Script] = []
    rejected: list[dict[str, str]] = []
    for upload in files:
        name = (upload.filename or "upload.pdf")[:255]
        script = Script(bundle_id=bundle.id, original_filename=name, pdf_path="", status="queued")
        db.add(script)
        await db.flush()
        dest = script_pdf(settings.storage_dir, script.id)
        try:
            await save_upload(upload, dest, max_bytes=settings.max_upload_mb * 1024 * 1024,
                              magic=PDF_MAGIC)
        except UploadError as exc:
            await db.delete(script)
            rejected.append({"filename": name, "reason": str(exc)})
            continue
        script.pdf_path = str(dest)
        accepted.append(script)
    await record(db, admin.id, "upload_scripts", "bundle", bundle.id,
                 accepted=len(accepted), rejected=len(rejected))
    await db.commit()
    for script in accepted:
        request.app.state.runner.enqueue(script.id)
    return UploadResult(accepted=[script_row(s) for s in accepted], rejected=rejected)


@router.post("/bundles/{bundle_id}/retry-failed")
async def retry_failed(bundle_id: int, request: Request, admin: AdminUser,
                       db: DbSession) -> BundleSummary:
    bundle = await load_bundle(db, bundle_id)
    not_submitted(bundle)
    failed = [s for s in bundle.scripts if s.status == "failed"]
    for script in failed:
        script.status, script.error = "queued", None
    await record(db, admin.id, "retry_failed", "bundle", bundle.id, scripts=len(failed))
    await db.commit()
    for script in failed:
        request.app.state.runner.enqueue(script.id)
    return bundle_summary(await load_bundle(db, bundle.id))


@router.post("/exams/{exam_id}/export")
async def export_marks(exam_id: int, admin: AdminUser, db: DbSession,
                       settings: AppSettings) -> ExamDetail:
    """Write (or rewrite) the exam's mark sheet. Only allowed once every bundle is submitted
    and every script approved."""
    problems = await run_export(db, exam_id, settings, admin.id)
    if problems:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"message": "The marks cannot be exported yet.", "problems": problems})
    return await exam_detail(exam_id, admin, db)


@router.get("/exams/{exam_id}/export/file")
async def download_export(exam_id: int, _: AdminUser, db: DbSession) -> FileResponse:
    exam = await load_exam(db, exam_id)
    path = Path(exam.export_path) if exam.export_path else None
    if exam.exported_at is None or path is None or not await asyncio.to_thread(path.is_file):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No exported mark sheet for this exam.")
    return FileResponse(path, filename=path.name, media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))


@router.post("/bundles/{bundle_id}/reopen")
async def reopen_bundle(bundle_id: int, body: Reason, admin: AdminUser,
                        db: DbSession) -> BundleSummary:
    bundle = await load_bundle(db, bundle_id)
    if bundle.submitted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bundle is not submitted.")
    bundle.submitted_at = None
    bundle.submitted_by = None
    exam = bundle.exam
    if exam.exported_at is not None:  # the exported sheet is now out of date
        exam.exported_at = None
        exam.export_error = "A bundle was reopened; export again after it is resubmitted."
    await record(db, admin.id, "reopen_bundle", "bundle", bundle.id, reason=body.reason)
    await db.commit()
    return bundle_summary(await load_bundle(db, bundle.id))
