"""Run an exam export and record the outcome on the exam (and in the audit trail)."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit import record
from backend.app.excel_export import ExportError, export_exam
from backend.app.queries import load_exam_full
from backend.config import Settings


async def run_export(db: AsyncSession, exam_id: int, settings: Settings,
                     user_id: int | None) -> list[str]:
    """Export the exam; returns the problems that blocked it (empty list = exported)."""
    exam = await load_exam_full(db, exam_id)
    try:
        path = await asyncio.to_thread(export_exam, exam, export_dir=settings.export_dir,
                                       master_path=settings.excel_master_path)
    except ExportError as exc:
        exam.export_error = "; ".join(exc.problems)
        await record(db, user_id, "export_blocked", "exam", exam.id, problems=exc.problems)
        await db.commit()
        return exc.problems
    exam.exported_at = datetime.now(UTC)
    exam.export_path = str(path)
    exam.export_error = None
    await record(db, user_id, "export", "exam", exam.id)
    await db.commit()
    return []
