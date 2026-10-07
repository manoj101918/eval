import pytest
from openpyxl import Workbook

from backend.grading.scheme import SchemeError, load_scheme
from backend.grading.template import main as template_main
from backend.grading.template import write_template

HEADER = ["Question", "Max marks", "Type", "Correct option", "Model answer / key points",
          "Marking guidance", "Choice group"]


def xlsx(tmp_path, rows, header=HEADER, title_rows=0, exam=None, name="scheme.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"
    for _ in range(title_rows):
        ws.append(["Physics mid-term marking scheme"])
    ws.append(header)
    for r in rows:
        ws.append(r)
    if exam:
        ex = wb.create_sheet("Exam")
        for k, v in exam.items():
            ex.append([k, v])
    path = tmp_path / name
    wb.save(path)
    return path


def problems(tmp_path, rows, **kw):
    with pytest.raises(SchemeError) as exc:
        load_scheme(xlsx(tmp_path, rows, **kw))
    return exc.value.problems


def test_template_round_trip(tmp_path):
    path = tmp_path / "t.xlsx"
    write_template(path)
    scheme = load_scheme(path)
    assert [i.key for i in scheme.items] == ["1", "5a", "12", "31", "31or"]
    assert scheme.items[0].correct_option == "c"
    assert scheme.items[4].student_key == "31"
    assert scheme.total_marks == 11  # the OR alternative counts once


def test_template_cli_refuses_to_overwrite(tmp_path, capsys):
    path = tmp_path / "t.xlsx"
    assert template_main([str(path)]) == 0
    assert template_main([str(path)]) == 1


def test_reads_rows_and_exam_sheet(tmp_path):
    scheme = load_scheme(xlsx(tmp_path, [
        [13, 1, "MCQ", "(C)", None, None, None],
        ["34(a)(i)", 1, "short", None, "a, b, c, i", "1 mark for all four", None],
        [None, None, None, None, None, None, None],  # blank rows ignored
        ["35 (b)", 2.5, "numerical", None, "v = u + at = 10 m/s", None, None],
    ], exam={"Subject": "Physics", "Exam": "Mid 1"}))
    assert [(i.key, i.max_marks, i.qtype) for i in scheme.items] == [
        ("13", 1, "mcq"), ("34ai", 1, "short"), ("35b", 2.5, "numerical")]
    assert scheme.items[0].correct_option == "c"
    assert scheme.items[1].guidance == "1 mark for all four"
    assert (scheme.subject, scheme.exam) == ("Physics", "Mid 1")
    assert scheme.total_marks == 4.5


def test_columns_found_by_name_in_any_order_below_title_rows(tmp_path):
    header = ["Key points", "Q No", "Marks", "Type"]
    scheme = load_scheme(xlsx(tmp_path, [["Newton's first law", "4", 2, "short"]],
                              header=header, title_rows=2))
    item = scheme.items[0]
    assert (item.key, item.max_marks, item.model_answer) == ("4", 2, "Newton's first law")


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (["3", None, "short", None, "x"], "Max marks must be a number"),
        (["3", 2, "essay", None, "x"], "Type must be one of"),
        (["3", 1, "mcq", None, None], "MCQ needs a correct option"),
        (["3", 1, "mcq", "z", None], "MCQ needs a correct option"),
        (["3", 2, "short", None, None], "model answer / key points are required"),
        (["3", 1.25, "short", None, "x"], "multiple of 0.5"),
        (["3", 0, "short", None, "x"], "greater than 0"),
        ([None, 2, "short", None, "x"], "Question is empty"),
    ],
)
def test_row_problems_name_the_row(tmp_path, row, expected):
    found = problems(tmp_path, [row])
    assert len(found) == 1
    assert found[0].startswith("row 2")
    assert expected in found[0]


def test_all_problems_reported_together(tmp_path):
    found = problems(tmp_path, [["1", "x", "short", None, "a"], ["2", 1, "bad", None, "a"],
                                ["3", 1, "short", None, "fine"]])
    assert [p.split(" ")[1] for p in found] == ["2", "3"]


def test_duplicate_question(tmp_path):
    found = problems(tmp_path, [["5(a)", 2, "short", None, "x"], ["5 a", 2, "short", None, "y"]])
    assert found == ["row 3 (5 a): same question as row 2"]


def test_choice_group_marks_must_match(tmp_path):
    found = problems(tmp_path, [["31", 5, "long", None, "x", None, "g"],
                                ["31 OR", 3, "long", None, "y", None, "g"]])
    assert found == ["choice group 'g': alternatives must have the same max marks"]


def test_no_header(tmp_path):
    found = problems(tmp_path, [["1", 2, "short"]], header=["foo", "bar"])
    assert "no header row" in found[0]


def test_missing_type_column(tmp_path):
    found = problems(tmp_path, [["1", 2]], header=["Question", "Max marks"])
    assert found == ["missing column(s): qtype"]


def test_empty_scheme(tmp_path):
    assert problems(tmp_path, []) == ["no questions found below the header row"]


def test_not_an_excel_file(tmp_path):
    path = tmp_path / "scheme.xlsx"
    path.write_text("not excel")
    with pytest.raises(SchemeError, match="cannot open the file"):
        load_scheme(path)
