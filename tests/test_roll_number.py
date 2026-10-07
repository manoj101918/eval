import logging

import anthropic
import pytest

from backend.config import Settings
from backend.extract.roll_number import decode_qr, match_roll_number, read_roll_number
from backend.extract.vision import VisionClient
from tests import pages
from tests.fakes import FakeAnthropic, api_error, message, scripted

PATTERN = r"[A-Z0-9]{5,15}"
JPEG = b"\xff\xd8cover"


def vision(handler):
    fake = FakeAnthropic(handler)
    s = Settings(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                 vision_max_retries=1)
    return VisionClient(fake, s), fake


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("21CS045", "21CS045"),
        ("21cs045", "21CS045"),
        ("21-CS-045", "21CS045"),
        (" 21 CS 045 ", "21CS045"),
        ("ROLL:21CS045", "21CS045"),
        ('{"roll": "21CS045", "exam": "MID1"}', "21CS045"),
        ("STUDENT 21CS045", "21CS045"),  # word without digits is not a roll number
        ("21CS045 21CS046", None),  # ambiguous
        ("ABC", None),
        ("", None),
        (None, None),
    ],
)
def test_match_roll_number(text, expected):
    assert match_roll_number(text, PATTERN) == expected


def test_decode_qr():
    assert decode_qr(pages.cover_page("ROLL:21CS045")) == ["ROLL:21CS045"]
    assert decode_qr(pages.cover_page(None)) == []


async def test_qr_path_skips_vision():
    client, fake = vision(scripted())
    result = await read_roll_number(
        pages.cover_page("21CS045"), JPEG, vision=client, pattern=PATTERN
    )
    assert (result.value, result.source) == ("21CS045", "qr")
    assert fake.messages.calls == []
    assert result.usage.api_calls == 0


async def test_no_qr_falls_back_to_vision():
    client, fake = vision(scripted(message({"roll_number": "22EC101"})))
    result = await read_roll_number(pages.cover_page(None), JPEG, vision=client, pattern=PATTERN)
    assert (result.value, result.source) == ("22EC101", "vision")
    assert len(fake.messages.calls) == 1
    assert result.usage.api_calls == 1


async def test_unmatched_qr_falls_back_to_vision():
    client, fake = vision(scripted(message({"roll_number": "22EC101"})))
    result = await read_roll_number(
        pages.cover_page("https://college.example/booklet"), JPEG, vision=client, pattern=PATTERN
    )
    assert (result.value, result.source) == ("22EC101", "vision")


async def test_vision_returns_null():
    client, _ = vision(scripted(message({"roll_number": None})))
    result = await read_roll_number(pages.cover_page(None), JPEG, vision=client, pattern=PATTERN)
    assert (result.value, result.source) == (None, None)


async def test_vision_value_must_match_pattern():
    client, _ = vision(scripted(message({"roll_number": "AB"})))
    result = await read_roll_number(pages.cover_page(None), JPEG, vision=client, pattern=PATTERN)
    assert result.value is None


async def test_vision_failure_does_not_raise():
    client, _ = vision(lambda _r: api_error(anthropic.BadRequestError, 400))
    result = await read_roll_number(pages.cover_page(None), JPEG, vision=client, pattern=PATTERN)
    assert (result.value, result.source) == (None, None)


async def test_roll_number_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = vision(scripted(message({"roll_number": "22EC101"})))
    await read_roll_number(pages.cover_page("21CS045"), JPEG, vision=client, pattern=PATTERN)
    await read_roll_number(pages.cover_page(None), JPEG, vision=client, pattern=PATTERN)
    assert "21CS045" not in caplog.text
    assert "22EC101" not in caplog.text
