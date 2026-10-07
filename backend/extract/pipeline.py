"""Extraction pipeline: one answer script in, an ExtractionResult out."""

import asyncio
import logging
import time
from collections.abc import Sequence
from pathlib import Path

import groq

from backend.config import Settings, get_settings
from backend.extract.ingest import load_script
from backend.extract.normalize import normalize_question_label
from backend.extract.preprocess import PageImage, preprocess_page
from backend.extract.roll_number import RollNumberResult, read_roll_number
from backend.extract.schemas import (
    Answer,
    ExtractionResult,
    PageTranscription,
    UnassignedText,
    Usage,
)
from backend.extract.vision import VisionAuthError, VisionClient, VisionError, VisionResult

logger = logging.getLogger(__name__)

# Failures that mark one page as failed; anything else is a bug and propagates.
PAGE_FAILURES = (VisionError, groq.APIError, TimeoutError)


def merge_pages(
    pages: Sequence[tuple[int, PageTranscription | None]],
) -> tuple[dict[str, Answer], list[UnassignedText]]:
    """Merge per-page segments (in page order) into answers keyed by question number.

    A segment without a label continues the previous answer. After a failed page (None)
    the previous answer is unknown, so unlabelled text goes to `unassigned` instead.
    """
    answers: dict[str, Answer] = {}
    unassigned: list[UnassignedText] = []
    current: str | None = None

    for page_no, transcription in sorted(pages, key=lambda p: p[0]):
        if transcription is None:
            current = None
            continue
        for seg in transcription.segments:
            key = normalize_question_label(seg.question_number) or current
            if key is None:
                unassigned.append(
                    UnassignedText(page=page_no, text=seg.text, has_diagram=seg.has_diagram,
                                   illegible=seg.illegible)
                )
                continue
            current = key
            notes = [f"p{page_no}: {n}" for n in seg.illegible_notes]
            answer = answers.get(key)
            if answer is None:
                answers[key] = Answer(text=seg.text, pages=[page_no], has_diagram=seg.has_diagram,
                                      illegible=seg.illegible, illegible_notes=notes)
                continue
            if seg.text:
                answer.text = f"{answer.text}\n\n{seg.text}" if answer.text else seg.text
            if page_no not in answer.pages:
                answer.pages.append(page_no)
            answer.has_diagram |= seg.has_diagram
            answer.illegible |= seg.illegible
            answer.illegible_notes.extend(notes)

    return answers, unassigned


async def _transcribe(
    vision: VisionClient, page: PageImage
) -> tuple[int, VisionResult[PageTranscription] | None]:
    page_no = page.index + 1
    try:
        return page_no, await vision.transcribe_page(page.jpeg, page_number=page_no)
    except VisionAuthError:
        raise  # every other page would fail the same way
    except PAGE_FAILURES as exc:
        logger.warning("page %d failed after retries: %s", page_no, type(exc).__name__)
        return page_no, None


async def extract_script(
    paths: Sequence[Path],
    *,
    settings: Settings | None = None,
    vision: VisionClient | None = None,
) -> ExtractionResult:
    s = settings or get_settings()
    vision = vision or VisionClient.from_settings(s)
    start = time.perf_counter()

    raw_pages = await asyncio.to_thread(load_script, paths, dpi=s.pdf_render_dpi)
    pages = await asyncio.gather(
        *(
            asyncio.to_thread(preprocess_page, raw, max_px=s.image_max_px,
                              jpeg_quality=s.jpeg_quality, blank_ink_ratio=s.blank_ink_ratio)
            for raw in raw_pages
        )
    )

    cover_full_res = raw_pages[0].image if s.has_cover_page else None
    del raw_pages  # keep only the full-res cover in memory (for QR decoding)
    answer_pages = pages[1:] if s.has_cover_page else pages
    to_send = [p for p in answer_pages if not p.blank]
    blank_pages = [p.index + 1 for p in answer_pages if p.blank]

    async def no_roll_number() -> RollNumberResult:
        return RollNumberResult(None, None)

    roll_coro = (
        read_roll_number(cover_full_res, pages[0].jpeg, vision=vision,
                         pattern=s.roll_number_pattern)
        if cover_full_res is not None
        else no_roll_number()
    )
    tasks = [asyncio.ensure_future(roll_coro)]
    tasks += [asyncio.ensure_future(_transcribe(vision, p)) for p in to_send]
    try:
        roll, *transcribed = await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:  # a fatal error (e.g. bad credentials): stop the remaining calls
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    usage = Usage()
    usage.add(roll.usage)
    for _, result in transcribed:
        if result is not None:
            usage.add(result.usage)
    answers, unassigned = merge_pages(
        [(page_no, result.data if result else None) for page_no, result in transcribed]
    )
    failed_pages = [page_no for page_no, result in transcribed if result is None]

    elapsed = round(time.perf_counter() - start, 2)
    logger.info(
        "script extracted: %d pages, %d blank, %d sent, %d failed, %d answers, %.1fs",
        len(pages), len(blank_pages), len(to_send), len(failed_pages), len(answers), elapsed,
    )
    return ExtractionResult(
        roll_number=roll.value,
        roll_number_source=roll.source,
        pages_total=len(pages),
        cover_page=1 if s.has_cover_page else None,
        blank_pages=blank_pages,
        failed_pages=failed_pages,
        answers=answers,
        unassigned=unassigned,
        usage=usage,
        elapsed_seconds=elapsed,
    )
