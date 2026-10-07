"""Pydantic schemas for the extraction pipeline.

`PageTranscription` and `CoverPageInfo` are the vision model's output formats;
every model response is parsed and validated against one of them.
`ExtractionResult` is the pipeline's output and the contract for later phases.
"""

from typing import Literal

from pydantic import BaseModel, Field

# --- Vision model outputs ---------------------------------------------------


class PageSegment(BaseModel):
    question_number: str | None = Field(
        description=(
            "The question label written by the student at the start of this answer, exactly as "
            "written (e.g. '1', '2a', 'Q3(b)'). Use null if this text continues an answer from "
            "the previous page and has no label of its own."
        )
    )
    text: str = Field(
        description="Verbatim transcription of the handwritten answer text in reading order."
    )
    has_diagram: bool = Field(
        description="True if this part of the answer contains a diagram, graph, table or figure."
    )
    illegible: bool = Field(description="True if any part of this text could not be read.")
    illegible_notes: list[str] = Field(
        description="Short notes on where text was illegible, e.g. 'line 4, two words'."
    )


class PageTranscription(BaseModel):
    segments: list[PageSegment] = Field(
        description="Answer segments on this page, in the order they appear. Empty if no answers."
    )


class CoverPageInfo(BaseModel):
    roll_number: str | None = Field(
        description=(
            "The student's roll / registration / hall-ticket number as written on the cover "
            "page, with spaces removed. Null if it is not present or not clearly readable."
        )
    )


class OcrWord(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    offset: int = Field(ge=0)


class OcrLine(BaseModel):
    text: str
    polygon: list[float] = Field(min_length=8)
    offset: int = Field(ge=0)
    length: int = Field(ge=0)


class OcrPage(BaseModel):
    """One page as returned by the OCR service, validated before use."""

    angle: float = Field(ge=-180, le=360)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    lines: list[OcrLine]
    words: list[OcrWord]


class OrientationCheck(BaseModel):
    upright_tile: Literal[1, 2, 3, 4] = Field(
        description="Number of the tile in which the handwriting is upright and readable."
    )


# --- Pipeline output ---------------------------------------------------------


class Usage(BaseModel):
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.api_calls += other.api_calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens += other.cached_input_tokens


class Answer(BaseModel):
    text: str
    pages: list[int]
    has_diagram: bool = False
    illegible: bool = False
    illegible_notes: list[str] = Field(default_factory=list)
    # Why a teacher should look closely, e.g. a question label that reappears after other
    # answers (often a misread label merging unrelated text under one question).
    review_notes: list[str] = Field(default_factory=list)


class UnassignedText(BaseModel):
    """Text found before any question label (cannot be attributed to a question)."""

    page: int
    text: str
    has_diagram: bool = False
    illegible: bool = False


class ExtractionResult(BaseModel):
    roll_number: str | None
    roll_number_source: Literal["qr", "vision", "ocr"] | None
    pages_total: int
    page_rotation: int = Field(
        description="Degrees counter-clockwise that make the pages upright (applied locally "
        "for the vision model; handled by the service for OCR)."
    )
    cover_page: int | None
    blank_pages: list[int]
    failed_pages: list[int]
    answers: dict[str, Answer]
    unassigned: list[UnassignedText]
    usage: Usage
    elapsed_seconds: float
