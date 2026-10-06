import io

import pytest
from PIL import Image

from backend.extract.ingest import RawPage
from backend.extract.preprocess import downscale, ink_ratio, preprocess_page, to_jpeg
from tests import pages

THRESHOLD = 0.0001  # the default BLANK_INK_RATIO


def test_downscale_limits_longest_side():
    img = Image.new("L", (1654, 2339), 255)
    out = downscale(img, 1500)
    assert max(out.size) == 1500
    assert out.size[0] == round(1654 * 1500 / 2339)


def test_downscale_never_upscales():
    img = Image.new("L", (800, 600), 255)
    assert downscale(img, 1500).size == (800, 600)


def test_to_jpeg_is_grayscale_jpeg():
    data = to_jpeg(Image.new("RGB", (100, 100), (200, 10, 10)), quality=80)
    decoded = Image.open(io.BytesIO(data))
    assert decoded.format == "JPEG"
    assert decoded.mode == "L"


def test_lower_quality_is_smaller():
    img = downscale(pages.written_page(), 1500)
    assert len(to_jpeg(img, 40)) < len(to_jpeg(img, 90))


@pytest.mark.parametrize(
    ("factory", "blank"),
    [
        (pages.blank_page, True),
        (pages.ruled_blank_page, True),
        (pages.short_answer_page, False),
        (pages.written_page, False),
    ],
)
def test_blank_detection(factory, blank):
    ratio = ink_ratio(downscale(factory(), 1500))
    assert (ratio < THRESHOLD) is blank, ratio


def test_written_page_has_much_more_ink_than_short_answer():
    full = ink_ratio(downscale(pages.written_page(), 1500))
    short = ink_ratio(downscale(pages.short_answer_page(), 1500))
    assert full > 20 * short


def test_preprocess_page():
    page = preprocess_page(
        RawPage(index=3, image=pages.written_page()),
        max_px=1500,
        jpeg_quality=80,
        blank_ink_ratio=THRESHOLD,
    )
    assert page.index == 3
    assert max(page.width, page.height) == 1500
    assert page.blank is False
    assert page.jpeg[:2] == b"\xff\xd8"  # JPEG magic
