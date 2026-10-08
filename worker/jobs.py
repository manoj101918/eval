"""Background job: transcribe and grade one uploaded script.

process_script: PDF -> extraction (phase 1) -> page images for the review screen ->
grading against the exam's marking scheme (phase 2) -> proposed marks in the database.
Errors are stored as short codes; logs carry only IDs and error types (no student data).
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.models import QuestionMark, Script
from backend.app.queries import load_script
from backend.app.storage import page_image
from backend.config import Settings
from backend.extract.clients import make_client
from backend.extract.ingest import IngestError
from backend.extract.ingest import load_script as load_pages
from backend.extract.orientation import rotate
from backend.extract.pipeline import extract_script
from backend.extract.preprocess import downscale, to_jpeg
from backend.grading.grader import Grader
from backend.grading.scheme import MarkingScheme
from backend.llm.groq_chat import VisionAuthError
from worker.runner import Handler

logger = logging.getLogger(__name__)


def save_page_images(settings: Settings, script_id: int, pdf: Path, rotation: int) -> int:
    """Upright, review-size JPEGs of every page; returns the page count."""
    pages = load_pages([pdf], dpi=settings.pdf_render_dpi)
    for raw in pages:
        image = downscale(rotate(raw.image, rotation), settings.page_image_max_px)
        dest = page_image(settings.storage_dir, script_id, raw.index + 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(to_jpeg(image, 80))
    return len(pages)


def make_handler(state: Any) -> Handler:
    """One set of AI clients per runner, so their rate limits cover every job."""
    clients: dict[str, Any] = {}

    def vision():
        if "vision" not in clients:
            factory = state.extraction_factory or make_client
            clients["vision"] = factory(state.settings)
        return clients["vision"]

    def grader():
        if "grader" not in clients:
            factory = state.grader_factory or Grader.from_settings
            clients["grader"] = factory(state.settings)
        return clients["grader"]

    async def handler(script_id: int) -> None:
        await process_script(state, script_id, vision, grader)

    async def aclose() -> None:
        """Close the AI clients' network sessions (called when the runner stops)."""
        for client in clients.values():
            await client.aclose()
        clients.clear()

    handler.aclose = aclose  # type: ignore[attr-defined]
    return handler


async def _fail(state: Any, script_id: int, code: str) -> None:
    async with state.sessionmaker() as db:
        script = await db.get(Script, script_id)
        if script is not None:
            script.status, script.error = "failed", code
            await db.commit()
    logger.warning("script %d failed: %s", script_id, code)


async def process_script(state: Any, script_id: int, vision, grader) -> None:
    settings: Settings = state.settings
    async with state.sessionmaker() as db:
        script = await db.get(Script, script_id)
        if script is None or script.status != "queued":
            return  # deleted, or already handled
        script.status, script.error = "processing", None
        await db.commit()
        script = await load_script(db, script_id)
        pdf = Path(script.pdf_path)
        scheme = MarkingScheme.model_validate_json(script.bundle.exam.scheme_json)

    try:
        transcriber, marker = vision(), grader()
        extraction = await extract_script([pdf], settings=settings, vision=transcriber)
        if transcriber.daily_limit_reached:
            await _fail(state, script_id, "quota")  # retry later rather than half-graded
            return
        page_count = await asyncio.to_thread(save_page_images, settings, script_id, pdf,
                                             extraction.page_rotation)
        result = await marker.grade(extraction, scheme)
        if marker.daily_limit_reached:
            await _fail(state, script_id, "quota")
            return
    except IngestError:
        await _fail(state, script_id, "unreadable_pdf")
        return
    except VisionAuthError:
        await _fail(state, script_id, "api_key")
        return
    except Exception as exc:  # recorded on the script; the runner keeps going
        await _fail(state, script_id, f"error:{type(exc).__name__}")
        return

    groups = {item.key: item.choice_group for item in scheme.items}
    async with state.sessionmaker() as db:
        script = await load_script(db, script_id)
        script.questions.clear()
        for position, q in enumerate(result.questions):
            script.questions.append(QuestionMark(
                position=position, question=q.question, key=q.key, qtype=q.qtype,
                max_marks=q.max_marks, choice_group=groups.get(q.key), ai_status=q.status,
                ai_marks=q.marks, ai_reason=q.reason, ai_confidence=q.confidence,
                points_met=q.points_met, points_missing=q.points_missing,
                needs_review=q.needs_review, review_notes=q.review_notes, pages=q.pages,
                final_marks=q.marks, confirmed=False,
            ))
        if script.roll_number_source != "teacher":
            script.roll_number = extraction.roll_number
            script.roll_number_source = extraction.roll_number_source
        script.page_count = page_count
        script.page_rotation = extraction.page_rotation
        script.extraction_json = extraction.model_dump_json()
        script.grading_json = result.model_dump_json()
        script.status, script.error = "graded", None
        script.graded_at = datetime.now(UTC)
        await db.commit()
    logger.info("script %d graded: %d questions", script_id, len(result.questions))
