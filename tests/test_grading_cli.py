import json

import groq
import pytest
from openpyxl import Workbook

from backend.config import Settings
from backend.extract.schemas import Answer, ExtractionResult, Usage
from backend.extract.vision import VisionClient
from backend.grading.__main__ import main
from backend.grading.grader import Grader
from tests import pages
from tests.fakes import FakeGroq, api_error, message, page_number_of


def write_scheme(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"
    ws.append(["Question", "Max marks", "Type", "Correct option", "Model answer / key points"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


@pytest.fixture
def scheme(tmp_path):
    return write_scheme(tmp_path / "scheme.xlsx", [
        ["1", 1, "mcq", "c", None],
        ["5", 3, "long", None, "three key points"],
    ])


@pytest.fixture
def script_json(tmp_path):
    result = ExtractionResult(
        roll_number="21CS045", roll_number_source="qr", pages_total=3, page_rotation=0,
        cover_page=1, blank_pages=[], failed_pages=[], unassigned=[], usage=Usage(),
        elapsed_seconds=1.0,
        answers={"1c": Answer(text="", pages=[2]), "5": Answer(text="my answer", pages=[2])},
    )
    path = tmp_path / "result.json"
    path.write_text(result.model_dump_json(), encoding="utf-8")
    return path


def settings():
    return Settings(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                    vision_max_retries=0, transcribe_provider="groq", page_rotation="0")


def grade(marks):
    return message({"marks_awarded": marks, "points_met": [], "points_missing": [],
                    "reason": "ok", "confidence": "high", "ocr_problem": False})


def grader_factory(handler):
    return lambda s: Grader(FakeGroq(handler), s)


def test_grades_from_extraction_json(scheme, script_json, capsys):
    code = main(["--scheme", str(scheme), "--script", str(script_json)], settings=settings(),
                grader_factory=grader_factory(lambda r: grade(2.5)))
    out, err = capsys.readouterr()
    assert code == 0
    data = json.loads(out)
    assert data["status"] == "proposed"
    assert data["roll_number"] == "21CS045"
    assert (data["total_marks"], data["max_marks"]) == (3.5, 4)
    assert "Proposed total: 3.5 / 4" in err
    assert "Not final until a teacher approves" in err


def test_grades_from_scan(scheme, tmp_path, capsys):
    scan = tmp_path / "scan"
    scan.mkdir()
    pages.cover_page("21CS045").save(scan / "p1.jpg")
    pages.written_page(5).save(scan / "p2.jpg")

    def vision_handler(request):
        assert page_number_of(request) == 2
        return message({"segments": [
            {"question_number": "1", "text": "(c)", "has_diagram": False, "illegible": False,
             "illegible_notes": []},
            {"question_number": "5", "text": "answer", "has_diagram": False,
             "illegible": False, "illegible_notes": []}]})

    code = main(["--scheme", str(scheme), str(scan)], settings=settings(),
                grader_factory=grader_factory(lambda r: grade(3)),
                extract_factory=lambda s: VisionClient(FakeGroq(vision_handler), s))
    out, _ = capsys.readouterr()
    assert code == 0
    assert json.loads(out)["total_marks"] == 4


def test_scheme_problems_exit_1(tmp_path, script_json, capsys):
    bad = write_scheme(tmp_path / "bad.xlsx", [["5", "lots", "long", None, "x"]])
    code = main(["--scheme", str(bad), "--script", str(script_json)], settings=settings(),
                grader_factory=grader_factory(lambda r: grade(1)))
    _, err = capsys.readouterr()
    assert code == 1
    assert "row 2 (5): Max marks must be a number" in err


def test_failed_question_exit_2(scheme, script_json, capsys):
    code = main(["--scheme", str(scheme), "--script", str(script_json)], settings=settings(),
                grader_factory=grader_factory(lambda r: api_error(groq.InternalServerError, 500)))
    out, _ = capsys.readouterr()
    assert code == 2
    assert json.loads(out)["failed"] == ["5"]


def test_missing_groq_key_exit_1(scheme, script_json, capsys):
    code = main(["--scheme", str(scheme), "--script", str(script_json)], settings=settings())
    _, err = capsys.readouterr()
    assert code == 1
    assert "No Groq API key" in err


def test_bad_extraction_json_exit_1(scheme, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    code = main(["--scheme", str(scheme), "--script", str(bad)], settings=settings())
    assert code == 1


@pytest.mark.parametrize("extra", [[], ["--script", "x.json", "scan.pdf"]])
def test_needs_exactly_one_input(scheme, extra):
    with pytest.raises(SystemExit) as exc:
        main(["--scheme", str(scheme), *extra], settings=settings())
    assert exc.value.code == 2
