import itertools

import pytest

from backend.extract.normalize import normalize_question_label
from backend.extract.schemas import Answer, ExtractionResult, Usage
from backend.grading.mapping import match_answers, mcq_option_from_text
from backend.grading.scheme import MarkingScheme, SchemeItem

_rows = itertools.count(2)


def item(label, qtype="short", marks=2, option=None, group=None):
    return SchemeItem(row=next(_rows), label=label, key=normalize_question_label(label),
                      max_marks=marks, qtype=qtype, correct_option=option,
                      model_answer="" if qtype == "mcq" else "model", choice_group=group)


def extraction(**answers):
    return ExtractionResult(
        roll_number=None, roll_number_source=None, pages_total=5, page_rotation=0,
        cover_page=1, blank_pages=[], failed_pages=[], unassigned=[], usage=Usage(),
        elapsed_seconds=1.0,
        answers={k: (a if isinstance(a, Answer) else Answer(text=a, pages=[2]))
                 for k, a in answers.items()},
    )


def match(items, **answers):
    result = match_answers(extraction(**answers), MarkingScheme(items=items))
    return {m.item.label: m for m in result.items}, result.unmatched


def test_exact_match():
    got, unmatched = match([item("34(a)(i)")], **{"34ai": "a, b, c, i"})
    assert got["34(a)(i)"].text == "a, b, c, i"
    assert got["34(a)(i)"].source_keys == ["34ai"]
    assert unmatched == []


def test_mcq_option_read_as_sub_part():
    got, unmatched = match([item("13", "mcq", 1, "d")], **{"13d": "explanation"})
    assert got["13"].mcq_option == "d"
    assert unmatched == []


@pytest.mark.parametrize(
    ("text", "option"),
    [("(c) 4.5 eV", "c"), ("c) 4.5", "c"), ("C", "c"), ("Ans: (b)", "b"), ("option d.", "d"),
     ("4.5 eV", None), ("because", None)],
)
def test_mcq_option_from_text(text, option):
    assert mcq_option_from_text(text) == option


def test_mcq_without_option_is_noted():
    got, _ = match([item("2", "mcq", 1, "a")], **{"2": "the current increases"})
    assert got["2"].mcq_option is None
    assert "no option letter (a-e) found in the answer" in got["2"].notes


def test_detailed_sub_parts_combined_under_scheme_row():
    got, unmatched = match([item("34(a)", marks=3)],
                           **{"34a": "points:", "34ai": "a, b, c", "34aiii": "d, e"})
    assert got["34(a)"].source_keys == ["34a", "34ai", "34aiii"]
    assert got["34(a)"].text == "points:\n\na, b, c\n\nd, e"
    got, unmatched = match([item("34(a)", marks=3)], **{"34ai": "a, b, c", "34aiii": "d, e"})
    assert got["34(a)"].text == "a, b, c\n\nd, e"
    assert unmatched == []


def test_more_specific_scheme_row_keeps_its_sub_part():
    got, unmatched = match([item("34(a)", marks=2), item("34(a)(iii)", marks=1)],
                           **{"34a": "points:", "34ai": "a, b", "34aiii": "d, e"})
    assert got["34(a)"].source_keys == ["34a", "34ai"]
    assert got["34(a)(iii)"].source_keys == ["34aiii"]
    assert unmatched == []


def test_or_alternatives_share_sub_parts():
    got, _ = match([item("31", group="g"), item("31 OR", group="g")],
                   **{"31a": "part a", "31b": "part b"})
    assert got["31"].text == got["31 OR"].text == "part a\n\npart b"


def test_numbered_sub_parts_after_letters_are_children():
    got, unmatched = match([item("31(a)(i)", marks=3)],
                           **{"31ai1": "differences", "31ai2": "fringe width"})
    assert got["31(a)(i)"].source_keys == ["31ai1", "31ai2"]
    assert unmatched == []


def test_children_do_not_cross_question_numbers():
    got, unmatched = match([item("3")], **{"34": "other question"})
    assert not got["3"].attempted
    assert [u.key for u in unmatched] == ["34"]


def test_unlabelled_sub_parts_graded_from_parent():
    got, _ = match([item("34(a)"), item("34(b)")], **{"34": "whole answer"})
    for label in ("34(a)", "34(b)"):
        assert got[label].text == "whole answer"
        assert any("not labelled separately" in n for n in got[label].notes)


def test_or_alternatives_both_get_the_answer():
    got, _ = match([item("31", group="g"), item("31 OR", group="g")], **{"31": "answer"})
    assert got["31"].text == got["31 OR"].text == "answer"


def test_not_attempted_and_unmatched():
    got, unmatched = match([item("5")], **{"7": "stray"})
    assert not got["5"].attempted
    assert "not attempted (or not found in the scan)" in got["5"].notes
    assert [(u.key, u.chars) for u in unmatched] == [("7", 5)]


def test_flags_and_notes_carried_over():
    answer = Answer(text="t", pages=[3, 4], has_diagram=True, illegible=True,
                    illegible_notes=["p3: unclear words: wrath"],
                    review_notes=["label seen again on page 4 after other answers"])
    got, _ = match([item("6")], **{"6": answer})
    m = got["6"]
    assert (m.has_diagram, m.illegible, m.pages) == (True, True, [3, 4])
    assert m.notes == ["p3: unclear words: wrath", "label seen again on page 4 after other answers"]
