import json

import groq
import pytest
from PIL import Image

from backend.config import Settings
from backend.extract import inspect as inspect_cli
from backend.extract.__main__ import main
from backend.extract.vision import VisionClient
from tests import pages
from tests.fakes import FakeGroq, api_error, message, page_number_of


@pytest.fixture(scope="module")
def script_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("cli")
    pages.cover_page("21CS045").save(d / "p1.jpg")
    pages.written_page(5).save(d / "p2.jpg")
    pages.ruled_blank_page().save(d / "p3.jpg")
    return d


def settings():
    return Settings(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                    vision_max_retries=0)


def factory(handler):
    def make(s):
        return VisionClient(FakeGroq(handler), s)

    return make


def ok_handler(request):
    assert page_number_of(request) == 2
    return message({"segments": [{"question_number": "1", "text": "answer",
                                  "has_diagram": False, "illegible": False,
                                  "illegible_notes": []}]})


def test_cli_prints_json_and_time(script_dir, capsys):
    code = main([str(script_dir)], settings=settings(), vision_factory=factory(ok_handler))
    out, err = capsys.readouterr()
    assert code == 0
    data = json.loads(out)
    assert data["roll_number"] == "21CS045"
    assert data["answers"]["1"]["text"] == "answer"
    assert data["blank_pages"] == [3]
    assert "Total time:" in err
    assert "3 pages, 1 blank, 0 failed, 1 API calls" in err


def test_cli_pretty(script_dir, capsys):
    main([str(script_dir), "--pretty"], settings=settings(), vision_factory=factory(ok_handler))
    out, _ = capsys.readouterr()
    assert out.startswith("{\n  ")


def test_cli_concurrency_flag(script_dir, capsys):
    seen = {}

    def make(s):
        seen["limit"] = s.vision_max_concurrency
        return VisionClient(FakeGroq(ok_handler), s)

    main([str(script_dir), "--concurrency", "2"], settings=settings(), vision_factory=make)
    assert seen["limit"] == 2


def test_cli_failed_pages_exit_2(script_dir, capsys):
    code = main([str(script_dir)], settings=settings(),
                vision_factory=factory(lambda r: api_error(groq.InternalServerError, 500)))
    out, _ = capsys.readouterr()
    assert code == 2
    assert json.loads(out)["failed_pages"] == [2]


def test_cli_ingest_error_exit_1(tmp_path, capsys):
    code = main([str(tmp_path / "missing.pdf")], settings=settings(),
                vision_factory=factory(ok_handler))
    _, err = capsys.readouterr()
    assert code == 1
    assert "error:" in err


def test_inspect_prints_ratios_without_roll_number(script_dir, capsys, monkeypatch):
    monkeypatch.setattr(inspect_cli, "get_settings", lambda: Settings(_env_file=None))
    assert inspect_cli.main([str(script_dir)]) == 0
    out, _ = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 4  # header + 3 pages
    assert "QR with valid roll number" in lines[1]
    assert lines[2].endswith("sent")
    assert lines[3].endswith("BLANK - skipped")
    assert "21CS045" not in out


def test_inspect_threshold_override(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(inspect_cli, "get_settings",
                        lambda: Settings(_env_file=None, has_cover_page=False))
    Image.new("L", (400, 400), 255).save(tmp_path / "a.png")
    inspect_cli.main([str(tmp_path), "--threshold", "0"])
    out, _ = capsys.readouterr()
    assert out.strip().splitlines()[1].endswith("sent")  # nothing is below a 0 threshold


def test_cli_auth_error_exit_1(script_dir, capsys):
    code = main([str(script_dir)], settings=settings(),
                vision_factory=factory(lambda r: api_error(groq.AuthenticationError, 401)))
    out, err = capsys.readouterr()
    assert code == 1
    assert out == ""
    assert "GROQ_API_KEY" in err
