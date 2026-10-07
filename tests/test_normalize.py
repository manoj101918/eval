import pytest

from backend.extract.normalize import normalize_question_label, qualify_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("1", "1"),
        ("10", "10"),
        ("1a", "1a"),
        ("1 a", "1a"),
        ("1(a)", "1a"),
        ("Q.1(a)", "1a"),
        ("Q1a", "1a"),
        ("q 3 (b)", "3b"),
        ("Ans 2", "2"),
        ("Ans: 2", "2"),
        ("Answer-4", "4"),
        ("Question 5(a)(i)", "5ai"),
        ("Qn. 6", "6"),
        ("2.ii", "2ii"),
        ("  7)  ", "7"),
        ("IV", "iv"),
        ("31 (1)", "31.1"),  # not question 311
        ("31. a) (i)", "31ai"),
        ("Q 4.2", "4.2"),
        ("3 1", "3.1"),
    ],
)
def test_normalize(label, expected):
    assert normalize_question_label(label) == expected


@pytest.mark.parametrize("label", [None, "", "   ", "Q", "Q.", "()", "SECTION-A", "Section B"])
def test_normalize_empty(label):
    assert normalize_question_label(label) is None


@pytest.mark.parametrize(
    ("key", "previous", "expected"),
    [
        ("ii", "31a", "31aii"),  # (ii) continuing 31 a
        ("b", "31a", "31b"),  # b) under question 31
        ("b", "31aii", "31b"),
        ("ii", "31", "31ii"),  # 31 (ii)
        ("ii", "31i", "31ii"),  # roman sub-part after roman sub-part
        ("32", "31a", "32"),  # already a full label
        ("31b", "31a", "31b"),
        ("b", None, "b"),  # no previous answer to attach to
        ("b", "iv", "b"),  # previous key has no question number
    ],
)
def test_qualify_label(key, previous, expected):
    assert qualify_label(key, previous) == expected
