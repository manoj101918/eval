import base64
import io
import logging

import groq
import pytest
from PIL import Image

from backend.config import Settings
from backend.extract.pipeline import extract_script, merge_pages
from backend.extract.schemas import PageSegment, PageTranscription
from backend.extract.vision import VisionAuthError, VisionClient
from tests import pages
from tests.fakes import (
    TPD_MESSAGE,
    FakeGroq,
    api_error,
    is_orientation_check,
    message,
    page_number_of,
)

ROLL = "21CS045"


def seg(q, text, diagram=False, illegible_notes=()):
    return {
        "question_number": q,
        "text": text,
        "has_diagram": diagram,
        "illegible": bool(illegible_notes),
        "illegible_notes": list(illegible_notes),
    }


def page(*segments):
    return PageTranscription.model_validate({"segments": list(segments)})


# Script layout: 1 cover (QR) | 2 written | 3 blank ruled | 4 written | 5 written
TRANSCRIPTS = {
    2: {"segments": [seg("Q1", "one-a")]},
    4: {"segments": [seg(None, "one-b"), seg("Q.2", "two", diagram=True)]},
    5: {"segments": [seg("3", "three", illegible_notes=["line 2"])]},
}


@pytest.fixture(scope="module")
def script_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("script")
    images = [
        pages.cover_page(ROLL),
        pages.written_page(),
        pages.ruled_blank_page(),
        pages.written_page(10),
        pages.short_answer_page(),
    ]
    for i, img in enumerate(images, start=1):
        img.save(d / f"page{i}.jpg", quality=92)
    return d


def settings(**overrides):
    base = dict(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                vision_max_retries=1, vision_max_concurrency=5, page_rotation="0")
    return Settings(**(base | overrides))


def by_page(transcripts=TRANSCRIPTS, failures=()):
    def handler(request):
        n = page_number_of(request)
        if n is None:
            return message({"roll_number": "SHOULD-NOT-BE-CALLED"})
        if n in failures:
            return api_error(groq.BadRequestError, 400)
        return message(transcripts[n], input_tokens=1000, output_tokens=100)

    return handler


async def run(script_dir, fake, **overrides):
    s = settings(**overrides)
    return await extract_script([script_dir], settings=s, vision=VisionClient(fake, s))


# --- merge_pages ---------------------------------------------------------------


def test_merge_continuation_joins_previous_answer():
    answers, unassigned = merge_pages(
        [(2, page(seg("1", "a"))), (3, page(seg(None, "b"), seg("2", "c")))]
    )
    assert answers["1"].text == "a\n\nb"
    assert answers["1"].pages == [2, 3]
    assert answers["2"].text == "c"
    assert unassigned == []


def test_merge_sorts_pages():
    answers, _ = merge_pages([(3, page(seg(None, "b"))), (2, page(seg("1", "a")))])
    assert answers["1"].text == "a\n\nb"


def test_merge_leading_unlabelled_text_is_unassigned():
    answers, unassigned = merge_pages([(2, page(seg(None, "orphan"), seg("1", "a")))])
    assert [u.text for u in unassigned] == ["orphan"]
    assert list(answers) == ["1"]


def test_merge_after_failed_page_continuation_is_unassigned():
    answers, unassigned = merge_pages(
        [(2, page(seg("1", "a"))), (3, None), (4, page(seg(None, "lost"), seg("2", "b")))]
    )
    assert answers["1"].text == "a"
    assert [(u.page, u.text) for u in unassigned] == [(4, "lost")]
    assert answers["2"].text == "b"


def test_merge_normalises_labels_and_revisits():
    answers, _ = merge_pages(
        [(2, page(seg("Q1", "a"), seg("Q.2", "b"))), (3, page(seg("Ans 1", "a-more")))]
    )
    assert answers["1"].text == "a\n\na-more"
    assert answers["1"].pages == [2, 3]
    assert list(answers) == ["1", "2"]


def test_merge_bare_q_label_is_continuation():
    answers, _ = merge_pages([(2, page(seg("1", "a"), seg("Q", "b")))])
    assert answers["1"].text == "a\n\nb"


def test_merge_flags_and_notes():
    answers, _ = merge_pages(
        [(2, page(seg("1", "a", illegible_notes=["line 1"]))),
         (3, page(seg(None, "b", diagram=True, illegible_notes=["line 9"])))]
    )
    a = answers["1"]
    assert a.has_diagram and a.illegible
    assert a.illegible_notes == ["p2: line 1", "p3: line 9"]


def test_page_segment_schema_requires_all_fields():
    with pytest.raises(ValueError):
        PageSegment.model_validate({"text": "x"})


# --- extract_script ------------------------------------------------------------


async def test_extract_script_end_to_end(script_dir):
    fake = FakeGroq(by_page())
    result = await run(script_dir, fake)

    assert result.roll_number == ROLL
    assert result.roll_number_source == "qr"
    assert result.pages_total == 5
    assert result.cover_page == 1
    assert result.blank_pages == [3]
    assert result.failed_pages == []
    assert sorted(page_number_of(c) for c in fake.completions.calls) == [2, 4, 5]  # no cover call

    assert result.answers["1"].text == "one-a\n\none-b"
    assert result.answers["1"].pages == [2, 4]
    assert result.answers["2"].has_diagram is True
    assert result.answers["3"].illegible_notes == ["p5: line 2"]
    assert result.usage.api_calls == 3
    assert result.usage.input_tokens == 3000
    assert result.elapsed_seconds > 0


async def test_out_of_order_completion_still_merges_in_page_order(script_dir):
    fake = FakeGroq(by_page(), delay=lambda r: 0.1 if page_number_of(r) == 2 else 0)
    result = await run(script_dir, fake)
    assert result.answers["1"].text == "one-a\n\none-b"


async def test_failed_page_is_reported_and_rest_completes(script_dir):
    fake = FakeGroq(by_page(failures={4}))
    result = await run(script_dir, fake)
    assert result.failed_pages == [4]
    assert result.answers["1"].text == "one-a"
    assert "3" in result.answers
    assert "2" not in result.answers


async def test_concurrency_limit_respected(script_dir):
    fake = FakeGroq(by_page(), delay=0.05)
    await run(script_dir, fake, vision_max_concurrency=2)
    assert fake.completions.peak_in_flight == 2


async def test_without_cover_page_all_pages_are_transcribed(script_dir):
    transcripts = dict(TRANSCRIPTS) | {1: {"segments": []}}
    fake = FakeGroq(by_page(transcripts))
    result = await run(script_dir, fake, has_cover_page=False)
    assert result.roll_number is None
    assert result.cover_page is None
    assert sorted(page_number_of(c) for c in fake.completions.calls) == [1, 2, 4, 5]


async def test_logs_never_contain_student_data(script_dir, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeGroq(by_page(failures={5}))
    await run(script_dir, fake)
    assert "page 5 failed" in caplog.text
    for secret in (ROLL, "one-a", "one-b", "two", "page1.jpg", str(script_dir)):
        assert secret not in caplog.text


async def test_auth_error_aborts_run_and_cancels_other_pages(script_dir):
    def handler(request):
        if page_number_of(request) == 2:
            return api_error(groq.AuthenticationError, 401)
        return message(TRANSCRIPTS[page_number_of(request)])

    finished = []

    def delay(request):
        return 0 if page_number_of(request) == 2 else 0.5

    fake = FakeGroq(handler, delay=delay)
    original_create = fake.completions.create

    async def tracking_create(**request):
        result = await original_create(**request)
        finished.append(page_number_of(request))
        return result

    fake.completions.create = tracking_create
    with pytest.raises(VisionAuthError):
        await run(script_dir, fake)
    assert finished == []  # slow pages were cancelled, not left running


def test_merge_qualifies_bare_sub_part_labels():
    answers, _ = merge_pages(
        [(2, page(seg("31 a", "part a"))), (3, page(seg("(ii)", "a-two"), seg("b)", "part b")))]
    )
    assert list(answers) == ["31a", "31aii", "31b"]


def oriented(tile, transcripts=TRANSCRIPTS):
    inner = by_page(transcripts)

    def handler(request):
        if is_orientation_check(request):
            return message({"upright_tile": tile}, input_tokens=1355, output_tokens=6)
        return inner(request)

    return handler


def jpeg_size(request):
    url = request["messages"][-1]["content"][0]["image_url"]["url"]
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size


async def test_auto_rotation_checks_once_and_rotates_all_pages(script_dir):
    fake = FakeGroq(oriented(tile=2))
    result = await run(script_dir, fake, page_rotation="auto")

    assert result.page_rotation == 90
    orientation_calls = [c for c in fake.completions.calls if is_orientation_check(c)]
    assert len(orientation_calls) == 1
    page_calls = [c for c in fake.completions.calls if page_number_of(c)]
    assert len(page_calls) == 3
    for call in page_calls:
        width, height = jpeg_size(call)
        assert width > height  # portrait scans turned to landscape
    assert result.usage.api_calls == 4
    assert result.answers["1"].text == "one-a\n\none-b"


async def test_auto_rotation_upright_keeps_pages(script_dir):
    fake = FakeGroq(oriented(tile=1))
    result = await run(script_dir, fake, page_rotation="auto")
    assert result.page_rotation == 0
    width, height = jpeg_size(next(c for c in fake.completions.calls if page_number_of(c)))
    assert height > width


async def test_forced_rotation_skips_check(script_dir):
    fake = FakeGroq(oriented(tile=1))
    result = await run(script_dir, fake, page_rotation="180")
    assert result.page_rotation == 180
    assert not any(is_orientation_check(c) for c in fake.completions.calls)



async def test_daily_limit_keeps_finished_pages(script_dir):
    def handler(request):
        n = page_number_of(request)
        if n == 2:
            return message(TRANSCRIPTS[2])
        return api_error(groq.RateLimitError, 429, message=TPD_MESSAGE)

    fake = FakeGroq(handler, delay=lambda r: 0 if page_number_of(r) == 2 else 0.05)
    result = await run(script_dir, fake, vision_max_concurrency=1)
    assert result.answers["1"].text == "one-a"
    assert result.failed_pages == [4, 5]
    assert len(fake.completions.calls) == 2  # page 5 was not sent after the daily limit
