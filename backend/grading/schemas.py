"""Grading schemas: the model's output per question, and the proposed result per script."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.extract.schemas import Usage
from backend.grading.mapping import UnmatchedAnswer

Confidence = Literal["high", "medium", "low"]


class QuestionGrade(BaseModel):
    """What the grading model returns for one answer (validated, then range-checked)."""

    marks_awarded: float = Field(
        description="Marks for this answer, in steps of 0.5, from 0 up to the maximum."
    )
    points_met: list[str] = Field(description="Marking-scheme points the answer earns.")
    points_missing: list[str] = Field(description="Marking-scheme points missing or wrong.")
    reason: str = Field(description="One or two plain sentences for the teacher.")
    confidence: Confidence = Field(
        description="high: clear-cut; medium: some judgement needed; low: unsure."
    )
    ocr_problem: bool = Field(
        description="True if the transcription is too garbled to grade reliably."
    )


class GradedQuestion(BaseModel):
    question: str  # label as in the marking scheme
    key: str
    max_marks: float
    qtype: str
    status: Literal["graded", "not_attempted", "failed"]
    marks: float | None  # None when grading failed (the teacher must grade it)
    counted: bool = True  # False for the OR alternative that was not used
    confidence: Confidence | None = None
    needs_review: bool
    reason: str = ""
    points_met: list[str] = Field(default_factory=list)
    points_missing: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)


class GradingResult(BaseModel):
    status: Literal["proposed"] = "proposed"  # never final: a teacher approves every mark
    roll_number: str | None
    subject: str | None
    exam: str | None
    total_marks: float  # sum of counted, graded questions
    max_marks: float
    questions: list[GradedQuestion]
    needs_review: int
    failed: list[str]  # questions the teacher must grade by hand
    unmatched_answers: list[UnmatchedAnswer]
    unassigned_pages: list[int]  # pages with text that belongs to no question label
    usage: Usage
    elapsed_seconds: float
