"""Database tables.

Workflow states are derived where possible (bundle and exam status are computed from their
scripts) so they cannot drift out of sync; only facts are stored (who submitted when, etc.).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))  # admin | teacher
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))  # e.g. "Mid-term 1"
    subject: Mapped[str] = mapped_column(String(120))
    class_section: Mapped[str] = mapped_column(String(60))  # e.g. "II CSE-A"
    scheme_json: Mapped[str] = mapped_column(Text)  # validated MarkingScheme
    scheme_filename: Mapped[str] = mapped_column(String(255))
    max_marks: Mapped[float] = mapped_column(Float)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_path: Mapped[str | None] = mapped_column(String(500))
    export_error: Mapped[str | None] = mapped_column(Text)

    bundles: Mapped[list["Bundle"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="Bundle.id")


class Bundle(Base):
    __tablename__ = "bundles"
    __table_args__ = (UniqueConstraint("exam_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    exam: Mapped[Exam] = relationship(back_populates="bundles")
    teacher: Mapped[User | None] = relationship(foreign_keys=[teacher_id])
    scripts: Mapped[list["Script"]] = relationship(
        back_populates="bundle", cascade="all, delete-orphan", order_by="Script.id")


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bundle_id: Mapped[int] = mapped_column(ForeignKey("bundles.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))  # may hold a name: never logged
    pdf_path: Mapped[str] = mapped_column(String(500))
    # queued | processing | graded | failed | approved
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(255))  # short code, never student data
    roll_number: Mapped[str | None] = mapped_column(String(32))
    # qr | ocr | vision | teacher
    roll_number_source: Mapped[str | None] = mapped_column(String(16))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    page_rotation: Mapped[int] = mapped_column(Integer, default=0)
    extraction_json: Mapped[str | None] = mapped_column(Text)
    grading_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    bundle: Mapped[Bundle] = relationship(back_populates="scripts")
    questions: Mapped[list["QuestionMark"]] = relationship(
        back_populates="script", cascade="all, delete-orphan", order_by="QuestionMark.position")


class QuestionMark(Base):
    __tablename__ = "question_marks"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # order in the marking scheme
    question: Mapped[str] = mapped_column(String(60))
    key: Mapped[str] = mapped_column(String(60))
    qtype: Mapped[str] = mapped_column(String(16))
    max_marks: Mapped[float] = mapped_column(Float)
    choice_group: Mapped[str | None] = mapped_column(String(60))
    ai_status: Mapped[str] = mapped_column(String(16))  # graded | not_attempted | failed
    ai_marks: Mapped[float | None] = mapped_column(Float)
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    ai_confidence: Mapped[str | None] = mapped_column(String(8))
    points_met: Mapped[list] = mapped_column(JSON, default=list)
    points_missing: Mapped[list] = mapped_column(JSON, default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_notes: Mapped[list] = mapped_column(JSON, default=list)
    pages: Mapped[list] = mapped_column(JSON, default=list)
    final_marks: Mapped[float | None] = mapped_column(Float)
    teacher_comment: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # teacher looked at it
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    script: Mapped[Script] = relationship(back_populates="questions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
