"""Turn OCR lines into answer segments: rebuild visual rows, find question labels, flag
diagrams and low-confidence words.

OCR returns lines in its own reading order, which can put a question number written in the
margin at the end of the page. Rows are therefore rebuilt from line positions, in the page's
reading frame (the page angle undone), so a margin label starts the row it belongs to.
Pages are segmented in page order with a carried label state, so "(b)" at the top of a page
continues the question from the previous page.
"""

import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extract.roll_number import match_roll_number
from backend.extract.schemas import OcrLine, OcrPage, PageSegment, PageTranscription

HEADER_ZONE = 0.12  # top/bottom fraction of the page holding page numbers and printed headers
HEADER_MAX_CHARS = 6  # only short rows there are dropped
ROW_OVERLAP = 0.5  # lines whose centres differ by less than this x height share a row
DIAGRAM_MIN_FRAGMENTS = 3
DIAGRAM_SHORT_CHARS = 4  # fragments this short are labels on a drawing, not prose
DIAGRAM_SHORT_SHARE = 0.6

MAIN_MARGIN = 0.04  # main question numbers sit within this fraction of the page's left text edge

_LETTERS = "abcdefgh"  # sub-part letters; i, v, x are read as roman numerals
_MAIN = re.compile(
    r"^(?:(?:question|ques|qn|q|answer|ans)\s*[.:\-]?\s*)?(\d{1,2})"
    r"(?:\s*(?:\.(?!\d)|[):\]])|(?=\s*\(?[a-hivx]{1,4}\)))",  # "34." "5)" "31.a)" - not "1.5"
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(r"\d{1,2}")  # "20" alone in the margin, e.g. beside a diagram
_SUB = re.compile(r"^[\s.]*\(?\s*([a-h]|i{1,3}|iv|vi{0,3}|ix|x)\s*\)", re.IGNORECASE)
_PAREN_NUM = re.compile(r"^[\s.]*\((\d{1,2})\)")
_ROLL_KEYWORDS = re.compile(r"roll|reg(?:istration|n)?\.?\s*no|hall\s*ticket|enrol", re.I)


@dataclass
class Fragment:
    x: float  # left edge in the reading frame
    text: str


@dataclass
class Row:
    y: float  # vertical centre in the reading frame
    height: float
    fragments: list[Fragment] = field(default_factory=list)
    line_offsets: list[tuple[int, int]] = field(default_factory=list)  # (offset, length)

    @property
    def text(self) -> str:
        return " ".join(f.text for f in sorted(self.fragments, key=lambda f: f.x)).strip()


@dataclass
class LabelState:
    main: str | None = None
    letter: str | None = None
    roman: str | None = None
    number: str | None = None  # "(2)" style sub-part

    def display(self) -> str:
        parts = [self.main or ""]
        if self.letter:
            parts.append(self.letter)
        if self.roman:
            parts.append(f"({self.roman})")
        if self.number:
            parts.append(f"({self.number})")
        return " ".join(p for p in parts if p)


# --- geometry ------------------------------------------------------------------------


def _reading_frame(polygon: Sequence[float], angle_deg: float) -> tuple[float, float, float]:
    """Left x, centre y and height of a polygon after undoing the page angle."""
    a = math.radians(angle_deg)
    cos_a, sin_a = math.cos(a), math.sin(a)
    xs, ys = [], []
    for i in range(0, len(polygon) - 1, 2):
        x, y = polygon[i], polygon[i + 1]
        xs.append(x * cos_a + y * sin_a)
        ys.append(-x * sin_a + y * cos_a)
    return min(xs), (min(ys) + max(ys)) / 2, max(ys) - min(ys)


def _frame_x_extent(page: OcrPage) -> tuple[float, float]:
    """(left x, width) of the page in the reading frame."""
    corners = [0, 0, page.width, 0, page.width, page.height, 0, page.height]
    a = math.radians(page.angle)
    xs = [corners[i] * math.cos(a) + corners[i + 1] * math.sin(a) for i in range(0, 8, 2)]
    return min(xs), max(max(xs) - min(xs), 1.0)


def _frame_extent(page: OcrPage) -> tuple[float, float]:
    """(min y, max y) of the page corners in the reading frame."""
    corners = [0, 0, page.width, 0, page.width, page.height, 0, page.height]
    a = math.radians(page.angle)
    ys = [-corners[i] * math.sin(a) + corners[i + 1] * math.cos(a) for i in range(0, 8, 2)]
    return min(ys), max(ys)


def build_rows(page: OcrPage) -> list[Row]:
    items = []
    for line in page.lines:
        x, y, h = _reading_frame(line.polygon, page.angle)
        items.append((y, x, max(h, 1.0), line))
    items.sort(key=lambda i: (i[0], i[1]))

    rows: list[Row] = []
    for y, x, h, line in items:
        row = rows[-1] if rows else None
        if row is not None and abs(y - row.y) <= ROW_OVERLAP * max(h, row.height):
            n = len(row.fragments)
            row.y = (row.y * n + y) / (n + 1)
            row.height = max(row.height, h)
        else:
            row = Row(y=y, height=h)
            rows.append(row)
        row.fragments.append(Fragment(x=x, text=line.text.strip()))
        row.line_offsets.append((line.offset, line.length))

    top, bottom = _frame_extent(page)
    span = max(bottom - top, 1.0)
    kept = []
    for row in rows:
        rel = (row.y - top) / span
        text = row.text
        in_margin_zone = rel < HEADER_ZONE or rel > 1 - HEADER_ZONE
        if in_margin_zone and len(text.replace(" ", "")) <= HEADER_MAX_CHARS:
            continue  # page number, printed header/footer
        if not re.search(r"[0-9A-Za-z]", text):
            continue  # stray marks
        kept.append(row)
    return kept


def is_diagram_row(row: Row) -> bool:
    frags = [f.text for f in row.fragments if f.text]
    if len(frags) < DIAGRAM_MIN_FRAGMENTS:
        return False
    short = sum(1 for t in frags if len(t) <= DIAGRAM_SHORT_CHARS)
    return short / len(frags) >= DIAGRAM_SHORT_SHARE


# --- labels ----------------------------------------------------------------------------


def parse_label(
    text: str, state: LabelState, *, allow_main: bool = True
) -> tuple[LabelState, str] | None:
    """If `text` starts with a question label, return the new label state and the rest.

    `allow_main` is False for rows indented from the margin: there a leading number is
    more likely an item of a list or part of an equation than a question number.
    """
    rest = text.lstrip()
    new: LabelState | None = None

    m = _MAIN.match(rest) if allow_main else None
    if m:
        new = LabelState(main=m.group(1))
        rest = rest[m.end():]

    while True:
        sub = _SUB.match(rest)
        num = _PAREN_NUM.match(rest)
        if sub:
            token = sub.group(1).lower()
            base = new or LabelState(main=state.main, letter=state.letter, roman=state.roman)
            if token in _LETTERS:
                new = LabelState(main=base.main, letter=token)
            else:
                new = LabelState(main=base.main, letter=base.letter, roman=token)
            rest = rest[sub.end():]
        elif num:
            base = new or LabelState(main=state.main, letter=state.letter, roman=state.roman,
                                     number=state.number)
            new = LabelState(main=base.main, letter=base.letter, roman=base.roman,
                             number=num.group(1))
            rest = rest[num.end():]
        else:
            break

    if new is None:
        return None
    return new, rest.strip()


# --- segmentation ----------------------------------------------------------------------


def _low_confidence_words(page: OcrPage, threshold: float) -> dict[int, list[str]]:
    """Low-confidence words keyed by the offset of the line that contains them."""
    by_line: dict[int, list[str]] = {}
    lines = sorted(page.lines, key=lambda ln: ln.offset)
    for word in page.words:
        if word.confidence >= threshold:
            continue
        for line in lines:
            if line.offset <= word.offset < line.offset + line.length:
                by_line.setdefault(line.offset, []).append(word.text)
                break
    return by_line


def segment_page(page: OcrPage, state: LabelState, *, low_confidence: float) -> PageTranscription:
    low = _low_confidence_words(page, low_confidence)
    segments: list[PageSegment] = []

    def current() -> PageSegment:
        if not segments:  # text before any label continues the previous page's answer
            segments.append(PageSegment(question_number=None, text="", has_diagram=False,
                                        illegible=False, illegible_notes=[]))
        return segments[-1]

    rows = build_rows(page)
    left_edge, frame_width = _frame_x_extent(page)
    text_left = min((min(f.x for f in r.fragments) for r in rows), default=left_edge)

    for row in rows:
        text = row.text
        first = min(row.fragments, key=lambda f: f.x)
        in_margin = first.x <= text_left + MAIN_MARGIN * frame_width
        if in_margin and _BARE_NUMBER.fullmatch(first.text):
            text = f"{first.text}. {text[len(first.text):].strip()}"
        parsed = parse_label(text, state, allow_main=in_margin)
        if parsed is not None:
            new_state, text = parsed
            state.main, state.letter = new_state.main, new_state.letter
            state.roman, state.number = new_state.roman, new_state.number
            segments.append(PageSegment(question_number=state.display(), text="",
                                        has_diagram=False, illegible=False, illegible_notes=[]))
        seg = current()
        if is_diagram_row(row):
            seg.has_diagram = True
            labels = text if parsed is not None else row.text
            text = f"[Diagram/equation fragments: {labels}]" if labels else ""
        if text:
            seg.text = f"{seg.text}\n{text}" if seg.text else text
        unclear = [w for off, _ in row.line_offsets for w in low.get(off, [])]
        if unclear:
            seg.illegible = True
            seg.illegible_notes.append("unclear words: " + ", ".join(unclear))

    return PageTranscription(segments=[s for s in segments if s.text or s.question_number])


def segment_pages(
    pages: Sequence[tuple[int, OcrPage | None]], *, low_confidence: float
) -> list[tuple[int, PageTranscription | None]]:
    """Segment OCR pages in page order, carrying the question-label state across pages.
    A failed page (None) resets the state: what it contained is unknown."""
    state = LabelState()
    out: list[tuple[int, PageTranscription | None]] = []
    for page_no, page in sorted(pages, key=lambda p: p[0]):
        if page is None:
            state = LabelState()
            out.append((page_no, None))
            continue
        out.append((page_no, segment_page(page, state, low_confidence=low_confidence)))
    return out


def script_rotation(angles: Sequence[float]) -> int:
    """Rotation (counter-clockwise, multiple of 90) that makes the script upright, from the
    text angles the OCR service measured per page."""
    if not angles:
        return 0
    return int(round(statistics.median(angles) / 90.0)) * 90 % 360


def find_roll_number(page: OcrPage, pattern: str) -> str | None:
    """Roll number from a cover page: only text on, or right after, a row that names the
    field ("Roll No", "Reg. No", "Hall Ticket"), so other numbers are not picked up."""
    rows = build_rows(page)
    for i, row in enumerate(rows):
        m = _ROLL_KEYWORDS.search(row.text)
        if not m:
            continue
        after = row.text[m.end():]
        found = match_roll_number(after, pattern)
        if found is None and i + 1 < len(rows):
            found = match_roll_number(rows[i + 1].text, pattern)
        if found:
            return found
    return None


def line_from_box(text: str, x: float, y: float, w: float, h: float, offset: int) -> OcrLine:
    """Build an axis-aligned OcrLine (used by tests and fakes)."""
    return OcrLine(text=text, polygon=[x, y, x + w, y, x + w, y + h, x, y + h], offset=offset,
                   length=len(text))
