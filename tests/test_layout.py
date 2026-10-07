import pytest

from backend.extract.azure_ocr import to_ocr_page
from backend.extract.layout import (
    LabelState,
    build_rows,
    find_roll_number,
    parse_label,
    script_rotation,
    segment_page,
    segment_pages,
)
from tests.azure_fakes import ocr_result

MARGIN = 100.0  # x of the left margin (question numbers)
BODY = 220.0  # x where answer text starts


def page(rows, **kw):
    return to_ocr_page(ocr_result(rows, **kw))


def segments(rows, state=None, **kw):
    t = segment_page(page(rows, **kw), state or LabelState(), low_confidence=0.5)
    return [(s.question_number, s.text) for s in t.segments]


# --- rows ------------------------------------------------------------------------------


def test_rows_put_margin_label_first_even_if_ocr_lists_it_last():
    rows = build_rows(page([[(MARGIN, "34."), (BODY, "Points having the same potential")]],
                           shuffle=True))
    assert [r.text for r in rows] == ["34. Points having the same potential"]


@pytest.mark.parametrize("angle", [0.0, 90.0, 180.0, 270.0, 89.6])
def test_rows_rebuilt_in_reading_order_for_rotated_pages(angle):
    rows = build_rows(page([[(MARGIN, "34."), (BODY, "first line")],
                            [(BODY, "second line")],
                            [(MARGIN, "b)"), (BODY, "third line")]], angle=angle))
    assert [r.text for r in rows] == ["34. first line", "second line", "b) third line"]


def test_page_numbers_and_stray_marks_dropped():
    rows = build_rows(page([(40.0, [(2200.0, "5")]),  # page number at the top
                            (300.0, [(BODY, "real answer text")]),
                            (400.0, [(BODY, ". ,")]),  # stray marks
                            (1650.0, [(1200.0, "12")])]))  # footer
    assert [r.text for r in rows] == ["real answer text"]


# --- labels ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "label", "rest"),
    [
        ("34. Points having", "34", "Points having"),
        ("Q5. Newton's law", "5", "Newton's law"),
        ("Ans 3) text", "3", "text"),
        ("31.a) (i) (1) INTERFERENCE", "31 a (i) (1)", "INTERFERENCE"),
        ("12 (b) text", "12 b", "text"),
    ],
)
def test_parse_main_labels(text, label, rest):
    state, remainder = parse_label(text, LabelState())
    assert (state.display(), remainder) == (label, rest)


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("b) Reff = 6", "34 b"),
        ("(ii) h, g, f", "34 a (ii)"),
        ("iii) d, e", "34 a (iii)"),
        ("(2) beta = lambda D / d", "34 a (2)"),
    ],
)
def test_parse_sub_labels_inherit_parent(text, label):
    state, _ = parse_label(text, LabelState(main="34", letter="a"))
    assert state.display() == label


@pytest.mark.parametrize("text", ["1.5 is the index", "3 - 2 = 1", "1/24 = (mu - 1)", "12",
                                  "Points having", "(at the junction)"])
def test_not_labels(text):
    assert parse_label(text, LabelState(main="20")) is None


def test_indented_number_is_not_a_question():
    assert parse_label("1) a, b, c", LabelState(main="34"), allow_main=False) is None


# --- segmentation ------------------------------------------------------------------------


def test_segments_split_on_labels_and_continue_text():
    got = segments([[(MARGIN, "34."), (BODY, "first")],
                    [(BODY, "more of 34")],
                    [(MARGIN, "a)"), (BODY, "part a")],
                    [(MARGIN, "b)"), (BODY, "part b")]])
    assert got == [("34", "first\nmore of 34"), ("34 a", "part a"), ("34 b", "part b")]


def test_text_before_any_label_is_a_continuation():
    got = segments([[(BODY, "continued from last page")], [(MARGIN, "5."), (BODY, "new")]])
    assert got == [(None, "continued from last page"), ("5", "new")]


def test_indented_list_number_stays_in_answer():
    got = segments([[(MARGIN, "34."), (BODY, "points are:")],
                    [(BODY + 60, "1) a, b, c")]])
    assert got == [("34", "points are:\n1) a, b, c")]


def test_bare_number_in_margin_is_a_label():
    got = segments([[(MARGIN, "19."), (BODY, "answer")], [(MARGIN, "20"), (BODY, "p-n junction")]])
    assert got[-1] == ("20", "p-n junction")


def test_diagram_rows_flagged():
    t = segment_page(page([[(MARGIN, "34."), (BODY, "circuit:")],
                           [(BODY, "1A"), (500.0, "b"), (800.0, "0.5A"), (1100.0, "R2")]]),
                     LabelState(), low_confidence=0.5)
    seg = t.segments[0]
    assert seg.has_diagram is True
    assert "[Diagram/equation fragments: 1A b 0.5A R2]" in seg.text


def test_low_confidence_words_flag_illegible():
    t = segment_page(page([[(MARGIN, "5."), (BODY, "the wrath of the maxima")]],
                          low_confidence={"wrath"}), LabelState(), low_confidence=0.5)
    seg = t.segments[0]
    assert seg.illegible is True
    assert seg.illegible_notes == ["unclear words: wrath"]


def test_state_carries_across_pages_and_resets_after_failure():
    p1 = page([[(MARGIN, "31."), (BODY, "a) part a")]])
    p2 = page([[(MARGIN, "(ii)"), (BODY, "more")]])
    p4 = page([[(MARGIN, "b)"), (BODY, "after a failed page")]])
    out = segment_pages([(4, p4), (2, p2), (3, None), (1, p1)], low_confidence=0.5)
    labels = {n: [s.question_number for s in t.segments] if t else None for n, t in out}
    assert labels == {1: ["31 a"], 2: ["31 a (ii)"], 3: None, 4: ["b"]}


# --- rotation & roll number --------------------------------------------------------------


@pytest.mark.parametrize(
    ("angles", "expected"),
    [([90.2, 89.8, 90.0], 90), ([], 0), ([0.4, -0.3], 0), ([-90.0], 270), ([179.0, 181.0], 180)],
)
def test_script_rotation(angles, expected):
    assert script_rotation(angles) == expected


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([[(BODY, "Roll No: 21CS045")]], "21CS045"),
        ([[(BODY, "Roll No.")], [(BODY, "21 CS 045")]], "21CS045"),
        ([[(BODY, "Hall Ticket No"), (900.0, "22EC101")]], "22EC101"),
        ([[(BODY, "Booklet No: 778812")], [(BODY, "Subject code 42")]], None),
    ],
)
def test_find_roll_number(rows, expected):
    assert find_roll_number(page(rows), r"[A-Z0-9]{5,15}") == expected
