"""Normalisation of student-written question labels to canonical keys."""

import re

_PREFIX = re.compile(r"^(question|ques|answer|ans|qn|q)(?=[\s.:\-)(\d]|$)\s*[.:\-)]*\s*")
_SECTION = re.compile(r"^(section|sec)\b")
_TOKENS = re.compile(r"\d+|[a-z]+")


def normalize_question_label(label: str | None) -> str | None:
    """Map labels like 'Q.1(a)', '1 a', 'Ans 1a' to a canonical key ('1a').

    Adjacent numbers keep a dot so '31 (1)' -> '31.1' stays distinct from question 311.
    Section headings ('SECTION-A') are not question labels. Returns None when nothing
    usable remains.
    """
    if label is None:
        return None
    s = label.strip().lower()
    if _SECTION.match(s):
        return None
    s = _PREFIX.sub("", s)
    key = ""
    for token in _TOKENS.findall(s):
        if key and token.isdigit() and key[-1].isdigit():
            key += "."
        key += token
    return key or None


_ROMAN = re.compile(r"[ivx]+")
_KEY_PARTS = re.compile(r"(\d+)([a-z])?")


def qualify_label(key: str, previous: str | None) -> str:
    """Add the parent question number to a bare sub-part label.

    A page that starts with "(ii)" or "b)" (the parent label is on an earlier page) gets the
    parent from the previous answer key: ("ii", "31a") -> "31aii", ("b", "31a") -> "31b".
    Keys that already start with a question number are returned unchanged.
    """
    if not key or key[0].isdigit() or previous is None:
        return key
    parts = _KEY_PARTS.match(previous)
    if parts is None:
        return key
    number, letter = parts.group(1), parts.group(2)
    if _ROMAN.fullmatch(key) and letter and not _ROMAN.fullmatch(letter):
        return f"{number}{letter}{key}"
    return f"{number}{key}"
