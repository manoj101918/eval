"""Match extracted answers (keys from the script) to marking-scheme questions."""

import re

from pydantic import BaseModel, Field

from backend.extract.schemas import Answer, ExtractionResult
from backend.grading.scheme import MCQ_OPTIONS, MarkingScheme, SchemeItem

_MCQ_IN_TEXT = re.compile(
    r"^\s*(?:ans(?:wer)?\s*[:.\-]?\s*)?(?:option\s*)?(?:\(\s*([a-e])\s*\)|([a-e])\s*[).:]|([a-e])\s*$)",
    re.IGNORECASE,
)


class MatchedItem(BaseModel):
    item: SchemeItem
    text: str = ""  # the student's answer text used for grading ("" = not attempted)
    source_keys: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    mcq_option: str | None = None
    has_diagram: bool = False
    illegible: bool = False
    notes: list[str] = Field(default_factory=list)  # why a teacher should look closely

    @property
    def attempted(self) -> bool:
        return bool(self.text.strip()) or self.mcq_option is not None


class UnmatchedAnswer(BaseModel):
    key: str
    pages: list[int]
    chars: int


class Matching(BaseModel):
    items: list[MatchedItem]
    unmatched: list[UnmatchedAnswer]


def _is_descendant(key: str, ancestor: str) -> bool:
    """'34ai' is below '34' and '34a'; '341' is not below '34' (a different question)."""
    if not key.startswith(ancestor) or key == ancestor:
        return False
    nxt = key[len(ancestor)]
    return not nxt.isdigit()


def mcq_option_from_text(text: str) -> str | None:
    m = _MCQ_IN_TEXT.match(text)
    if not m:
        return None
    letter = next(g for g in m.groups() if g)
    return letter.lower()


def _fill(matched: MatchedItem, keys: list[str], answers: dict[str, Answer]) -> None:
    texts = []
    for key in keys:
        answer = answers[key]
        texts.append(answer.text)
        matched.source_keys.append(key)
        matched.pages.extend(p for p in answer.pages if p not in matched.pages)
        matched.has_diagram |= answer.has_diagram
        matched.illegible |= answer.illegible
        matched.notes.extend(answer.illegible_notes)
        matched.notes.extend(answer.review_notes)
    matched.text = "\n\n".join(t for t in texts if t)


def match_answers(extraction: ExtractionResult, scheme: MarkingScheme) -> Matching:
    answers = extraction.answers
    order = list(answers)  # order of appearance in the script
    claimed: set[str] = set()
    results: dict[int, MatchedItem] = {}

    # Pass 1: exact keys, and MCQ answers whose option was read as a sub-part ("13d").
    for item in scheme.items:
        m = MatchedItem(item=item)
        sk = item.student_key
        if sk in answers:
            _fill(m, [sk], answers)
            claimed.add(sk)
        elif item.qtype == "mcq":
            suffixed = [k for k in order if len(k) == len(sk) + 1 and k.startswith(sk)
                        and k[-1] in MCQ_OPTIONS]
            if suffixed:
                _fill(m, suffixed[:1], answers)
                claimed.add(suffixed[0])
                m.mcq_option = suffixed[0][-1]
        if item.qtype == "mcq" and m.mcq_option is None and m.text:
            m.mcq_option = mcq_option_from_text(m.text)
            if m.mcq_option is None:
                m.notes.append("no option letter (a-e) found in the answer")
        results[item.row] = m

    # Pass 2: sub-parts written in more detail than the scheme ("34ai" for scheme "34a").
    for item in scheme.items:
        m = results[item.row]
        if m.source_keys:
            continue
        children = [k for k in order if k not in claimed and _is_descendant(k, item.student_key)]
        if children:
            _fill(m, children, answers)
            claimed.update(children)

    # Pass 3: sub-parts not labelled by the student ("34" written for scheme "34a", "34b").
    for item in scheme.items:
        m = results[item.row]
        if m.source_keys:
            continue
        ancestors = [k for k in order if _is_descendant(item.student_key, k)]
        if ancestors:
            parent = max(ancestors, key=len)  # the closest ancestor
            _fill(m, [parent], answers)
            claimed.add(parent)
            m.notes.append(f"sub-part not labelled separately; graded from the answer to "
                           f"{parent}")

    for m in results.values():
        if not m.attempted:
            m.notes.append("not attempted (or not found in the scan)")

    unmatched = [
        UnmatchedAnswer(key=k, pages=answers[k].pages, chars=len(answers[k].text))
        for k in order if k not in claimed
    ]
    return Matching(items=[results[i.row] for i in scheme.items], unmatched=unmatched)
