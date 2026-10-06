import asyncio
import base64
import logging

import anthropic
import pytest

from backend.config import Settings
from backend.extract.schemas import PageTranscription
from backend.extract.vision import InvalidOutputError, OutputRefusedError, VisionClient
from tests.fakes import FakeAnthropic, api_error, message, scripted

JPEG = b"\xff\xd8fake-jpeg"
SECRET_TEXT = "Photosynthesis converts light energy"

PAGE = {
    "segments": [
        {
            "question_number": "Q1",
            "text": SECRET_TEXT,
            "has_diagram": False,
            "illegible": False,
            "illegible_notes": [],
        }
    ]
}


def settings(**overrides):
    base = dict(
        _env_file=None,
        vision_backoff_base_s=0,
        vision_backoff_max_s=0,
        vision_max_retries=3,
        vision_call_timeout_s=5,
    )
    return Settings(**(base | overrides))


async def test_transcribe_page_success():
    fake = FakeAnthropic(scripted(message(PAGE, input_tokens=1500, output_tokens=300)))
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=2)

    assert isinstance(result.data, PageTranscription)
    assert result.data.segments[0].text == SECRET_TEXT
    assert result.usage.api_calls == 1
    assert result.usage.input_tokens == 1500

    request = fake.messages.calls[0]
    assert request["model"] == "claude-haiku-4-5"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    image = request["messages"][0]["content"][0]
    assert image["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(image["source"]["data"]) == JPEG
    assert "page 2 of the script" in request["messages"][0]["content"][1]["text"]
    schema = request["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert "segments" in schema["schema"]["properties"]


async def test_read_cover():
    fake = FakeAnthropic(scripted(message({"roll_number": "21CS045"})))
    result = await VisionClient(fake, settings()).read_cover(JPEG)
    assert result.data.roll_number == "21CS045"
    assert fake.messages.calls[0]["max_tokens"] == 256


async def test_rate_limit_then_success_counts_all_usage():
    fake = FakeAnthropic(
        scripted(
            api_error(anthropic.RateLimitError, 429),
            message("not json", input_tokens=10, output_tokens=5),
            message(PAGE, input_tokens=100, output_tokens=50),
        )
    )
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.messages.calls) == 3
    # The 429 returned no usage; the invalid response is billed and counted.
    assert result.usage.api_calls == 2
    assert result.usage.input_tokens == 110


async def test_bad_request_not_retried():
    fake = FakeAnthropic(scripted(api_error(anthropic.BadRequestError, 400)))
    with pytest.raises(anthropic.BadRequestError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.messages.calls) == 1


async def test_refusal_not_retried():
    fake = FakeAnthropic(scripted(message("", stop_reason="refusal")))
    with pytest.raises(OutputRefusedError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.messages.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        message('{"segments": [{"text": "x"}]}'),  # schema violation
        message("{not json"),
        message('{"segments": [', stop_reason="max_tokens"),  # truncated
    ],
)
async def test_invalid_output_retried_once_then_fails(bad):
    fake = FakeAnthropic(scripted(bad, bad, message(PAGE)))
    with pytest.raises(InvalidOutputError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.messages.calls) == 2


async def test_invalid_output_then_valid_succeeds():
    fake = FakeAnthropic(scripted(message("{not json"), message(PAGE)))
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert result.data.segments[0].question_number == "Q1"


async def test_validation_error_does_not_leak_output():
    leaky = message('{"segments": [{"text": "' + SECRET_TEXT + '"}]}')
    fake = FakeAnthropic(scripted(leaky, leaky))
    with pytest.raises(InvalidOutputError) as exc:
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert SECRET_TEXT not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


async def test_per_call_timeout_retries_then_fails():
    fake = FakeAnthropic(lambda _r: message(PAGE), delay=1.0)
    client = VisionClient(fake, settings(vision_call_timeout_s=0.02, vision_max_retries=2))
    with pytest.raises(TimeoutError):
        await client.transcribe_page(JPEG, page_number=1)
    assert len(fake.messages.calls) == 3


async def test_concurrency_is_bounded():
    fake = FakeAnthropic(lambda _r: message(PAGE), delay=0.02)
    client = VisionClient(fake, settings(vision_max_concurrency=3))
    await asyncio.gather(*(client.transcribe_page(JPEG, page_number=i) for i in range(10)))
    assert len(fake.messages.calls) == 10
    assert fake.messages.peak_in_flight == 3


async def test_logs_contain_no_student_text(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeAnthropic(scripted(message("{bad"), message(PAGE)))
    await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=4)
    assert "transcribe page 4" in caplog.text
    assert SECRET_TEXT not in caplog.text
