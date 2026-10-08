"""Loading helpers with the relationships the views need (async needs eager loading).

Views built after a change reuse the objects the request modified (the session keeps
them, expire_on_commit=False). Change relationships by assigning objects, not ids,
so loaded relationships never go stale."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models import Bundle, Exam, Script


async def load_exam(db: AsyncSession, exam_id: int) -> Exam:
    exam = await db.scalar(
        select(Exam).where(Exam.id == exam_id).options(
            selectinload(Exam.bundles).selectinload(Bundle.scripts),
            selectinload(Exam.bundles).selectinload(Bundle.teacher),
        ))
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    return exam


async def load_exam_full(db: AsyncSession, exam_id: int) -> Exam:
    """Exam with every bundle, script and question mark (for the export)."""
    exam = await db.scalar(
        select(Exam).where(Exam.id == exam_id).options(
            selectinload(Exam.bundles).selectinload(Bundle.scripts)
            .selectinload(Script.questions),
            selectinload(Exam.bundles).selectinload(Bundle.teacher),
        ).execution_options(populate_existing=True))
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    return exam


async def load_bundle(db: AsyncSession, bundle_id: int) -> Bundle:
    bundle = await db.scalar(
        select(Bundle).where(Bundle.id == bundle_id).options(
            selectinload(Bundle.scripts), selectinload(Bundle.teacher),
            selectinload(Bundle.exam).selectinload(Exam.bundles).selectinload(Bundle.scripts),
        ))
    if bundle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bundle not found.")
    return bundle


async def load_script(db: AsyncSession, script_id: int) -> Script:
    script = await db.scalar(
        select(Script).where(Script.id == script_id).options(
            selectinload(Script.questions),
            selectinload(Script.bundle).selectinload(Bundle.scripts),
            selectinload(Script.bundle).selectinload(Bundle.exam),
        ))
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found.")
    return script
