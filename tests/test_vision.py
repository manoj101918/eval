import asyncio
import base64
import json
import logging

import groq
import pytest
from pydantic import SecretStr

from backend.config import Settings
from backend.extract.schemas import CoverPageInfo, PageTranscription
from backend.extract.vision import (
    DailyLimitError,
    InvalidOutputError,
    VisionAuthError,
    VisionClient,
    rate_limit_kind,
    strict_json_schema,
)
from tests.fakes import (
    TPD_MESSAGE,
    TPM_MESSAGE,
    FakeGroq,
    api_error,
    connection_error,
    message,
    scripted,
    user_text,
)

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


def test_strict_schema_is_closed_and_inlined():
    schema = strict_json_schema(PageTranscription)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["segments"]
    segment = schema["properties"]["segments"]["items"]
    assert segment["additionalProperties"] is False
    assert set(segment["required"]) == set(segment["properties"]) == {
        "question_number", "text", "has_diagram", "illegible", "illegible_notes"
    }
    assert strict_json_schema(CoverPageInfo)["required"] == ["roll_number"]


async def test_transcribe_page_success():
    fake = FakeGroq(scripted(message(PAGE, input_tokens=1500, output_tokens=300, cached=1000)))
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=2)

    assert isinstance(result.data, PageTranscription)
    assert result.data.segments[0].text == SECRET_TEXT
    assert result.usage.api_calls == 1
    assert result.usage.input_tokens == 1500
    assert result.usage.cached_input_tokens == 1000

    request = fake.completions.calls[0]
    assert request["model"] == "qwen/qwen3.8-27b"
    assert request["reasoning_effort"] == "none"
    assert request["max_completion_tokens"] == 2048
    system, user = request["messages"]
    assert system["role"] == "system" and "transcribe" in system["content"]
    assert '"segments"' in system["content"]  # json_object mode: schema is in the prompt
    image = user["content"][0]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(image.split(",", 1)[1]) == JPEG
    assert "page 2 of the script" in user_text(request)
    assert request["response_format"] == {"type": "json_object"}


async def test_json_schema_mode_uses_strict_schema():
    fake = FakeGroq(scripted(message(PAGE)))
    client = VisionClient(fake, settings(vision_response_format="json_schema"))
    await client.transcribe_page(JPEG, page_number=1)
    request = fake.completions.calls[0]
    fmt = request["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "PageTranscription"
    assert '"segments"' not in request["messages"][0]["content"]


async def test_check_orientation():
    fake = FakeGroq(scripted(message({"upright_tile": 2})))
    result = await VisionClient(fake, settings()).check_orientation(JPEG)
    assert result.data.upright_tile == 2
    assert fake.completions.calls[0]["max_completion_tokens"] == 64


async def test_orientation_rejects_out_of_range_tile():
    fake = FakeGroq(scripted(message({"upright_tile": 5}), message({"upright_tile": 7})))
    with pytest.raises(InvalidOutputError):
        await VisionClient(fake, settings()).check_orientation(JPEG)


async def test_rate_limit_pauses_other_calls():
    """After a 429 with retry-after, queued calls wait instead of hitting the limit too."""
    times = []

    def handler(request):
        times.append(asyncio.get_running_loop().time())
        if len(times) == 1:
            return api_error(groq.RateLimitError, 429, {"retry-after": "0.3"})
        return message(PAGE)

    fake = FakeGroq(handler)
    client = VisionClient(fake, settings(vision_max_concurrency=1))
    await asyncio.gather(client.transcribe_page(JPEG, page_number=1),
                         client.transcribe_page(JPEG, page_number=2))
    assert len(times) == 3
    assert min(times[1:]) - times[0] >= 0.25


async def test_read_cover():
    fake = FakeGroq(scripted(message({"roll_number": "21CS045"})))
    result = await VisionClient(fake, settings()).read_cover(JPEG)
    assert result.data.roll_number == "21CS045"
    assert fake.completions.calls[0]["max_completion_tokens"] == 256


async def test_rate_limit_then_success_counts_all_usage():
    fake = FakeGroq(
        scripted(
            api_error(groq.RateLimitError, 429),
            message("not json", input_tokens=10, output_tokens=5),
            message(PAGE, input_tokens=100, output_tokens=50),
        )
    )
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.completions.calls) == 3
    # The 429 returned no usage; the invalid response is billed and counted.
    assert result.usage.api_calls == 2
    assert result.usage.input_tokens == 110


async def test_bad_request_not_retried():
    fake = FakeGroq(scripted(api_error(groq.BadRequestError, 400)))
    with pytest.raises(groq.BadRequestError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.completions.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        message('{"segments": [{"text": "x"}]}'),  # schema violation
        message("{not json"),
        message(None),  # no content
        message('{"segments": [', finish_reason="length"),  # truncated
    ],
)
async def test_invalid_output_retried_once_then_fails(bad):
    fake = FakeGroq(scripted(bad, bad, message(PAGE)))
    with pytest.raises(InvalidOutputError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.completions.calls) == 2


async def test_invalid_output_then_valid_succeeds():
    fake = FakeGroq(scripted(message("{not json"), message(PAGE)))
    result = await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert result.data.segments[0].question_number == "Q1"


async def test_validation_error_does_not_leak_output():
    leaky = message('{"segments": [{"text": "' + SECRET_TEXT + '"}]}')
    fake = FakeGroq(scripted(leaky, leaky))
    with pytest.raises(InvalidOutputError) as exc:
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert SECRET_TEXT not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


async def test_per_call_timeout_retries_then_fails():
    fake = FakeGroq(lambda _r: message(PAGE), delay=1.0)
    client = VisionClient(fake, settings(vision_call_timeout_s=0.02, vision_max_retries=2))
    with pytest.raises(TimeoutError):
        await client.transcribe_page(JPEG, page_number=1)
    assert len(fake.completions.calls) == 3


async def test_concurrency_is_bounded():
    fake = FakeGroq(lambda _r: message(PAGE), delay=0.02)
    client = VisionClient(fake, settings(vision_max_concurrency=3))
    await asyncio.gather(*(client.transcribe_page(JPEG, page_number=i) for i in range(10)))
    assert len(fake.completions.calls) == 10
    assert fake.completions.peak_in_flight == 3


async def test_logs_contain_no_student_text(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeGroq(scripted(message("{bad"), message(PAGE)))
    await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=4)
    assert "transcribe page 4" in caplog.text
    assert SECRET_TEXT not in caplog.text


@pytest.mark.parametrize(
    "error",
    [api_error(groq.AuthenticationError, 401), api_error(groq.PermissionDeniedError, 403)],
)
async def test_rejected_key_is_fatal_auth_error(error):
    fake = FakeGroq(scripted(error))
    with pytest.raises(VisionAuthError):
        await VisionClient(fake, settings()).transcribe_page(JPEG, page_number=1)
    assert len(fake.completions.calls) == 1


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key_fails_at_construction(key):
    s = settings(groq_api_key=SecretStr(key) if key is not None else None)
    with pytest.raises(VisionAuthError):
        VisionClient.from_settings(s)


def test_from_settings_disables_sdk_retries():
    client = VisionClient.from_settings(settings(groq_api_key=SecretStr("gsk_test")))
    assert client._client.max_retries == 0


async def test_connection_error_pauses_other_calls():
    """A network blip pauses every queued call instead of each burning its retries."""
    times = []

    def handler(request):
        times.append(asyncio.get_running_loop().time())
        if len(times) == 1:
            return connection_error()
        return message(PAGE)

    fake = FakeGroq(handler)
    client = VisionClient(fake, settings(vision_max_concurrency=1, vision_backoff_max_s=0.5))
    await asyncio.gather(client.transcribe_page(JPEG, page_number=1),
                         client.transcribe_page(JPEG, page_number=2))
    assert len(times) == 3
    assert min(times[1:]) - times[0] >= 0.25


async def test_rate_limit_pause_has_a_floor(monkeypatch):
    """Groq sometimes says retry-after: 1 when the window needs longer."""
    import backend.extract.vision as vision_module

    monkeypatch.setattr(vision_module, "RATE_LIMIT_MIN_PAUSE_S", 0.3)
    times = []

    def handler(request):
        times.append(asyncio.get_running_loop().time())
        if len(times) == 1:
            return api_error(groq.RateLimitError, 429, {"retry-after": "0.01"})
        return message(PAGE)

    client = VisionClient(FakeGroq(handler), settings(vision_max_concurrency=1))
    await client.transcribe_page(JPEG, page_number=1)
    assert times[1] - times[0] >= 0.25


async def test_token_pacing_spaces_requests(monkeypatch):
    """With a tiny tokens-per-minute budget the second call waits for the window."""
    import backend.extract.ratelimit as ratelimit_module

    monkeypatch.setattr(ratelimit_module, "WINDOW_S", 0.3)
    times = []

    def handler(request):
        times.append(asyncio.get_running_loop().time())
        return message(PAGE, input_tokens=900, output_tokens=100)

    client = VisionClient(FakeGroq(handler), settings(vision_tokens_per_minute=1000))
    await asyncio.gather(client.transcribe_page(JPEG, page_number=1),
                         client.transcribe_page(JPEG, page_number=2))
    assert times[1] - times[0] >= 0.25



def test_rate_limit_kind():
    assert rate_limit_kind(api_error(groq.RateLimitError, 429, message=TPD_MESSAGE)) == "TPD"
    assert rate_limit_kind(api_error(groq.RateLimitError, 429, message=TPM_MESSAGE)) == "TPM"
    assert rate_limit_kind(api_error(groq.RateLimitError, 429)) is None


async def test_daily_limit_fails_fast_and_skips_later_calls(caplog):
    fake = FakeGroq(scripted(api_error(groq.RateLimitError, 429, {"retry-after": "3600"},
                                       message=TPD_MESSAGE)))
    client = VisionClient(fake, settings())
    with pytest.raises(DailyLimitError):
        await client.transcribe_page(JPEG, page_number=1)
    assert client.daily_limit_reached is True
    with pytest.raises(DailyLimitError):
        await client.transcribe_page(JPEG, page_number=2)
    assert len(fake.completions.calls) == 1  # the second page was never sent
    assert "org_test" not in caplog.text


async def test_minute_limit_is_still_retried():
    fake = FakeGroq(scripted(api_error(groq.RateLimitError, 429, {"retry-after": "0"},
                                       message=TPM_MESSAGE), message(PAGE)))
    client = VisionClient(fake, settings())
    result = await client.transcribe_page(JPEG, page_number=1)
    assert result.data.segments
    assert client.daily_limit_reached is False
