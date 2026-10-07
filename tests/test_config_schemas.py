import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.extract.schemas import PageTranscription, Usage


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.vision_model == "qwen/qwen3.8-27b"
    assert s.vision_reasoning_effort == "none"
    assert s.image_max_px == 1500
    assert s.has_cover_page is True


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("VISION_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("HAS_COVER_PAGE", "false")
    s = Settings(_env_file=None)
    assert s.vision_max_concurrency == 3
    assert s.has_cover_page is False


def test_settings_rejects_bad_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, vision_max_concurrency=0)


def test_page_transcription_validates():
    page = PageTranscription.model_validate(
        {
            "segments": [
                {
                    "question_number": "1a",
                    "text": "Newton's first law...",
                    "has_diagram": False,
                    "illegible": False,
                    "illegible_notes": [],
                },
                {
                    "question_number": None,
                    "text": "continued",
                    "has_diagram": True,
                    "illegible": True,
                    "illegible_notes": ["last line"],
                },
            ]
        }
    )
    assert page.segments[1].question_number is None


def test_page_transcription_rejects_missing_fields():
    with pytest.raises(ValidationError):
        PageTranscription.model_validate({"segments": [{"text": "x"}]})


def test_usage_add():
    total = Usage()
    total.add(Usage(api_calls=1, input_tokens=10, output_tokens=5, cached_input_tokens=3))
    total.add(Usage(api_calls=1, input_tokens=1, output_tokens=1))
    assert total == Usage(api_calls=2, input_tokens=11, output_tokens=6, cached_input_tokens=3)
