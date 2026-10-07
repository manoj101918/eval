import asyncio
import logging

import pytest
from azure.core.exceptions import ServiceRequestError
from pydantic import SecretStr

from backend.config import Settings
from backend.extract.azure_ocr import AzureOCRClient, to_ocr_page
from backend.extract.vision import (
    DailyLimitError,
    InvalidOutputError,
    VisionAuthError,
    VisionError,
)
from tests.azure_fakes import FakeAzure, http_error, ocr_result, scripted

JPEG = b"\xff\xd8fake-jpeg"
SECRET_TEXT = "Photosynthesis converts light energy"
PAGE = ocr_result([[(100.0, "5."), (220.0, SECRET_TEXT)]], angle=90.0)
QUOTA_MESSAGE = "Out of call volume quota for FormRecognizer F0 pricing tier."


def settings(**overrides):
    base = dict(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                vision_max_retries=3, vision_call_timeout_s=5)
    return Settings(**(base | overrides))


def client(handler, delay=0.0, **overrides):
    fake = FakeAzure(handler, delay=delay)
    return AzureOCRClient(fake, settings(**overrides)), fake


def test_to_ocr_page_validates_sdk_result():
    page = to_ocr_page(PAGE)
    assert page.angle == 90.0
    assert [ln.text for ln in page.lines] == ["5.", SECRET_TEXT]
    assert page.words[0].confidence == pytest.approx(0.98)


def test_to_ocr_page_rejects_bad_result_without_leaking_text():
    broken = ocr_result([[(100.0, SECRET_TEXT)]])
    broken.pages[0].lines[0].polygon = [1.0, 2.0]  # too few points
    with pytest.raises(InvalidOutputError) as exc:
        to_ocr_page(broken)
    assert SECRET_TEXT not in str(exc.value)


async def test_transcribe_page_success():
    ocr, fake = client(scripted(PAGE))
    result = await ocr.transcribe_page(JPEG, page_number=2)
    assert result.data.lines[1].text == SECRET_TEXT
    assert result.usage.api_calls == 1
    assert fake.calls[0]["body"] == JPEG
    assert fake.calls[0]["model_id"] == "prebuilt-read"


async def test_rate_limit_retried_with_retry_after():
    ocr, fake = client(scripted(http_error(429, headers={"Retry-After": "0"}), PAGE))
    result = await ocr.transcribe_page(JPEG, page_number=1)
    assert result.data.lines
    assert len(fake.calls) == 2


async def test_server_error_retried():
    ocr, fake = client(scripted(http_error(503), http_error(500), PAGE))
    await ocr.transcribe_page(JPEG, page_number=1)
    assert len(fake.calls) == 3


async def test_connection_error_retried():
    ocr, fake = client(scripted(ServiceRequestError("connection reset"), PAGE))
    await ocr.transcribe_page(JPEG, page_number=1)
    assert len(fake.calls) == 2


async def test_bad_request_not_retried():
    ocr, fake = client(scripted(http_error(400, "InvalidImage")))
    with pytest.raises(VisionError, match=r"\(400\)"):
        await ocr.transcribe_page(JPEG, page_number=1)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_key_is_fatal(status):
    ocr, fake = client(scripted(http_error(status, "Access denied")))
    with pytest.raises(VisionAuthError):
        await ocr.transcribe_page(JPEG, page_number=1)
    assert len(fake.calls) == 1


async def test_quota_exhausted_skips_later_calls(caplog):
    ocr, fake = client(scripted(http_error(403, QUOTA_MESSAGE)))
    with pytest.raises(DailyLimitError):
        await ocr.transcribe_page(JPEG, page_number=1)
    assert ocr.daily_limit_reached is True
    with pytest.raises(DailyLimitError):
        await ocr.transcribe_page(JPEG, page_number=2)
    assert len(fake.calls) == 1


async def test_failure_during_polling_is_handled():
    fake = FakeAzure(scripted(http_error(503), PAGE), fail_on_submit=False)
    ocr = AzureOCRClient(fake, settings())
    result = await ocr.transcribe_page(JPEG, page_number=1)
    assert result.data.lines
    assert len(fake.calls) == 2


async def test_timeout_retried_then_fails():
    ocr, fake = client(lambda _b, _i: PAGE, delay=1.0, vision_call_timeout_s=0.02,
                       vision_max_retries=2)
    with pytest.raises(TimeoutError):
        await ocr.transcribe_page(JPEG, page_number=1)
    assert len(fake.calls) == 3


async def test_concurrency_is_bounded():
    ocr, fake = client(lambda _b, _i: PAGE, delay=0.02, azure_max_concurrency=2)
    await asyncio.gather(*(ocr.transcribe_page(JPEG, page_number=i) for i in range(6)))
    assert fake.peak_in_flight == 2


async def test_rate_limit_pauses_other_calls(monkeypatch):
    import backend.extract.azure_ocr as azure_module

    monkeypatch.setattr(azure_module, "RATE_LIMIT_MIN_PAUSE_S", 0.3)

    def handler(_body, i):
        return http_error(429, headers={"Retry-After": "0"}) if i == 1 else PAGE

    ocr, fake = client(handler)
    await asyncio.gather(ocr.transcribe_page(JPEG, page_number=1),
                         ocr.transcribe_page(JPEG, page_number=2))
    assert min(c["t"] for c in fake.calls[1:]) - fake.calls[0]["t"] >= 0.25


async def test_read_cover_finds_roll_number():
    cover = ocr_result([[(220.0, "Roll No: 21CS045")]])
    ocr, _ = client(scripted(cover))
    result = await ocr.read_cover(JPEG)
    assert result.data.roll_number == "21CS045"


async def test_logs_contain_no_student_text(caplog):
    caplog.set_level(logging.DEBUG)
    ocr, _ = client(scripted(http_error(503), PAGE))
    await ocr.transcribe_page(JPEG, page_number=3)
    assert "ocr page 3" in caplog.text
    assert SECRET_TEXT not in caplog.text


def test_from_settings_requires_endpoint_and_key():
    with pytest.raises(VisionAuthError):
        AzureOCRClient.from_settings(settings())
    with pytest.raises(VisionAuthError):
        AzureOCRClient.from_settings(settings(azure_di_endpoint="https://x.cognitiveservices.azure.com/"))


async def test_from_settings_builds_client():
    ocr = AzureOCRClient.from_settings(settings(
        azure_di_endpoint="https://x.cognitiveservices.azure.com/",
        azure_di_key=SecretStr("k" * 32),
    ))
    assert ocr.handles_rotation is True
    await ocr.aclose()
