"""Normalisation of student-written question labels to canonical keys."""

import re

_PREFIX = re.compile(r"^(question|ques|answer|ans|qn|q)(?=[\s.:\-)(\d]|$)\s*[.:\-)]*\s*")
_NON_ALNUM = re.compile(r"[^0-9a-z]")


def normalize_question_label(label: str | None) -> str | None:
    """Map labels like 'Q.1(a)', '1 a', 'Ans 1a' to a canonical key ('1a').

    Returns None when nothing usable remains.
    """
    if label is None:
        return None
    s = label.strip().lower()
    s = _PREFIX.sub("", s)
    s = _NON_ALNUM.sub("", s)
    return s or None
