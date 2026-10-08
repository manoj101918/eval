"""Workflow status, derived from the scripts so it can never drift out of sync.

Bundle:  open -> grading -> ready -> in_review -> submitted
Exam:    draft -> grading -> in_review -> completed -> exported
"""

from collections import Counter

from backend.app.models import Bundle, Exam

PENDING = {"queued", "processing"}


def script_counts(bundle: Bundle) -> Counter:
    return Counter(s.status for s in bundle.scripts)


def bundle_status(bundle: Bundle) -> str:
    if bundle.submitted_at is not None:
        return "submitted"
    counts = script_counts(bundle)
    if not counts:
        return "open"
    if any(counts[s] for s in PENDING):
        return "grading"
    if counts["approved"]:
        return "in_review"
    return "ready"


def exam_status(exam: Exam) -> str:
    if exam.exported_at is not None:
        return "exported"
    statuses = [bundle_status(b) for b in exam.bundles]
    if not statuses or all(s == "open" for s in statuses):
        return "draft"
    if all(s == "submitted" for s in statuses):
        return "completed"
    if any(s in ("grading", "open") for s in statuses):
        return "grading"
    return "in_review"
