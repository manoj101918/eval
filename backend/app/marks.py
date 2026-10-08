"""Mark arithmetic shared by review and export: OR groups and totals."""

from collections.abc import Sequence

from backend.app.models import QuestionMark


def counted_ids(questions: Sequence[QuestionMark], *, use_final: bool = True) -> set[int]:
    """Questions that count towards the total: every question outside an OR group, and the
    best-scoring alternative of each group (first one on a tie)."""
    counted: set[int] = set()
    best: dict[str, QuestionMark] = {}
    for q in questions:
        if q.choice_group is None:
            counted.add(q.id)
            continue
        value = (q.final_marks if use_final else q.ai_marks) or 0.0
        current = best.get(q.choice_group)
        current_value = ((current.final_marks if use_final else current.ai_marks) or 0.0) \
            if current else -1.0
        if value > current_value:
            best[q.choice_group] = q
    counted.update(q.id for q in best.values())
    return counted


def total(questions: Sequence[QuestionMark], *, use_final: bool = True) -> float:
    ids = counted_ids(questions, use_final=use_final)
    return sum((q.final_marks if use_final else q.ai_marks) or 0.0
               for q in questions if q.id in ids)


def valid_marks(value: float, max_marks: float) -> bool:
    return 0 <= value <= max_marks and (value * 2) % 1 == 0


def approval_problems(questions: Sequence[QuestionMark], roll_number: str | None) -> list[str]:
    """Why a script cannot be approved yet (empty list = ready)."""
    problems = []
    if not roll_number:
        problems.append("Enter the roll number.")
    ids = counted_ids(questions)
    for q in questions:
        if q.final_marks is None and q.id in ids:
            problems.append(f"Enter marks for question {q.question}.")
        if q.needs_review and not q.confirmed and q.id in ids:
            problems.append(f"Check question {q.question} (flagged for review).")
    return problems
