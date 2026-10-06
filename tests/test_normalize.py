import pytest

from backend.extract.normalize import normalize_question_label


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
    ],
)
def test_normalize(label, expected):
    assert normalize_question_label(label) == expected


@pytest.mark.parametrize("label", [None, "", "   ", "Q", "Q.", "()"])
def test_normalize_empty(label):
    assert normalize_question_label(label) is None
