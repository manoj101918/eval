"""Grade one script: match answers to the marking scheme, grade MCQs by rule and other
answers with the grading model, and return proposed marks for teacher review."""

import asyncio
import logging
import re
import time

from backend.config import Settings
from backend.extract.schemas import ExtractionResult, Usage
from backend.grading import prompts
from backend.grading.mapping import MatchedItem, match_answers
from backend.grading.schemas import GradedQuestion, GradingResult, QuestionGrade
from backend.grading.scheme import MarkingScheme
from backend.llm.groq_chat import (
    PAGE_FAILURES,
    GroqChat,
    GroqChatConfig,
    GroqLike,
    VisionAuthError,
    make_async_groq,
)

logger = logging.getLogger(__name__)

_ANSWER_TAG = re.compile(r"</?\s*answer\s*>", re.IGNORECASE)
MAX_RANGE_RETRIES = 1


class GradeRangeError(Exception):
    """The model's marks were outside 0..max or not in steps of 0.5."""


def grading_chat_config(s: Settings) -> GroqChatConfig:
    return GroqChatConfig(
        model=s.grading_model,
        reasoning_effort=s.grading_reasoning_effort,
        response_format=s.grading_response_format,
        max_concurrency=s.grading_max_concurrency,
        tokens_per_minute=s.grading_tokens_per_minute,
        call_timeout_s=s.vision_call_timeout_s,
        max_retries=s.vision_max_retries,
        rate_limit_retries=s.vision_rate_limit_retries,
        backoff_base_s=s.vision_backoff_base_s,
        backoff_max_s=s.vision_backoff_max_s,
        temperature=s.grading_temperature,
    )


def _fmt_marks(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _check_range(grade: QuestionGrade, max_marks: float) -> None:
    m = grade.marks_awarded
    if m < 0 or m > max_marks or (m * 2) % 1:
        raise GradeRangeError(f"marks {m} not in 0..{max_marks} in steps of 0.5")


class Grader:
    def __init__(self, client: GroqLike, settings: Settings) -> None:
        self._settings = settings
        self._chat = GroqChat(client, grading_chat_config(settings))

    @classmethod
    def from_settings(cls, settings: Settings) -> "Grader":
        key = settings.groq_api_key.get_secret_value() if settings.groq_api_key else None
        return cls(make_async_groq(key, settings.vision_call_timeout_s), settings)

    @property
    def daily_limit_reached(self) -> bool:
        return self._chat.daily_limit_reached

    async def aclose(self) -> None:
        await self._chat.aclose()

    # --- single question -----------------------------------------------------------------

    def _grade_mcq(self, m: MatchedItem) -> GradedQuestion:
        item = m.item
        correct = m.mcq_option == item.correct_option
        if m.mcq_option is None:
            reason = "No option letter found in the answer; check the script."
        elif correct:
            reason = f"Chose ({m.mcq_option}), the correct option."
        else:
            reason = f"Chose ({m.mcq_option}); the correct option is ({item.correct_option})."
        return self._result(
            m, status="graded", marks=item.max_marks if correct else 0.0,
            confidence="high" if m.mcq_option else "low", reason=reason,
            needs_review=m.mcq_option is None or bool(m.notes),
        )

    async def _grade_text(self, m: MatchedItem, usage: Usage) -> GradedQuestion:
        item = m.item
        user = prompts.GRADING_USER.format(
            label=item.label, max_marks=_fmt_marks(item.max_marks), qtype=item.qtype,
            model_answer=item.model_answer or "(none)", guidance=item.guidance or "(none)",
            answer=_ANSWER_TAG.sub("[tag removed]", m.text),
        )
        for attempt in range(MAX_RANGE_RETRIES + 1):
            result = await self._chat.complete(
                system=prompts.GRADING_SYSTEM, user_content=user, output_model=QuestionGrade,
                max_tokens=self._settings.grading_max_tokens, label=f"grade question {item.label}",
            )
            usage.add(result.usage)
            grade = result.data
            try:
                _check_range(grade, item.max_marks)
                break
            except GradeRangeError:
                if attempt == MAX_RANGE_RETRIES:
                    logger.warning("question %s: marks out of range twice", item.label)
                    return self._result(m, status="failed", marks=None, needs_review=True,
                                        reason="The model's marks were out of range; "
                                               "grade by hand.")
        needs_review = (grade.confidence == "low" or grade.ocr_problem or m.has_diagram
                        or m.illegible or bool(m.notes))
        q = self._result(m, status="graded", marks=grade.marks_awarded,
                         confidence=grade.confidence, reason=grade.reason,
                         needs_review=needs_review)
        q.points_met, q.points_missing = grade.points_met, grade.points_missing
        if grade.ocr_problem:
            q.review_notes.append("transcription too garbled to grade reliably")
        if m.has_diagram:
            q.review_notes.append("answer has a diagram or equation the grader could not see")
        return q

    def _result(self, m: MatchedItem, *, status, marks, needs_review, reason="",
                confidence=None) -> GradedQuestion:
        item = m.item
        return GradedQuestion(
            question=item.label, key=item.key, max_marks=item.max_marks, qtype=item.qtype,
            status=status, marks=marks, confidence=confidence, needs_review=needs_review,
            reason=reason, review_notes=list(m.notes), source_keys=m.source_keys, pages=m.pages,
        )

    async def _grade_one(self, m: MatchedItem, usage: Usage, check_missing: bool) -> GradedQuestion:
        if not m.attempted:
            return self._result(
                m, status="not_attempted", marks=0.0, needs_review=check_missing,
                reason="No answer found for this question.",
            )
        if m.item.qtype == "mcq":
            return self._grade_mcq(m)
        try:
            return await self._grade_text(m, usage)
        except VisionAuthError:
            raise
        except PAGE_FAILURES as exc:
            logger.warning("question %s: grading failed (%s)", m.item.label, type(exc).__name__)
            return self._result(m, status="failed", marks=None, needs_review=True,
                                reason="Automatic grading failed; grade by hand.")

    # --- whole script ----------------------------------------------------------------------

    async def grade(self, extraction: ExtractionResult, scheme: MarkingScheme) -> GradingResult:
        start = time.perf_counter()
        matching = match_answers(extraction, scheme)
        usage = Usage()
        # An unanswered question may be an answer the scan lost: worth a look when the
        # script has failed pages or text that matched no question.
        check_missing = bool(extraction.failed_pages or extraction.unassigned
                             or matching.unmatched)
        tasks = [asyncio.ensure_future(self._grade_one(m, usage, check_missing))
                 for m in matching.items]
        try:
            questions = list(await asyncio.gather(*tasks))
        except BaseException:
            for t in tasks:  # fatal (e.g. bad key): stop the remaining calls
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        self._apply_choice_groups(questions, scheme)
        total = sum(q.marks or 0.0 for q in questions if q.counted and q.marks is not None)
        failed = [q.question for q in questions if q.status == "failed"]
        elapsed = round(time.perf_counter() - start, 2)
        logger.info("script graded: %d questions, %d need review, %d failed, %.1fs",
                    len(questions), sum(q.needs_review for q in questions if q.counted),
                    len(failed), elapsed)
        return GradingResult(
            roll_number=extraction.roll_number, subject=scheme.subject, exam=scheme.exam,
            total_marks=total, max_marks=scheme.total_marks, questions=questions,
            needs_review=sum(q.needs_review for q in questions if q.counted),
            failed=failed, unmatched_answers=matching.unmatched,
            unassigned_pages=sorted({u.page for u in extraction.unassigned}),
            usage=usage, elapsed_seconds=elapsed,
        )

    @staticmethod
    def _apply_choice_groups(questions: list[GradedQuestion], scheme: MarkingScheme) -> None:
        """In each OR group only the best-scoring alternative counts."""
        groups: dict[str, list[GradedQuestion]] = {}
        for item, q in zip(scheme.items, questions, strict=True):
            if item.choice_group:
                groups.setdefault(item.choice_group, []).append(q)
        for alternatives in groups.values():
            best = max(alternatives, key=lambda q: (q.marks is not None, q.marks or 0.0))
            graded = [q for q in alternatives if q.status != "not_attempted"]
            for q in alternatives:
                q.counted = q is best
                if not q.counted:
                    q.needs_review = False
            if len(graded) > 1:
                best.review_notes.append("graded against each OR alternative; the higher "
                                         "mark is used")
