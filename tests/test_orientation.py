import io

import groq
import pytest
from PIL import Image

from backend.config import Settings
from backend.extract.orientation import build_mosaic, detect_rotation, rotate
from backend.extract.vision import VisionAuthError, VisionClient
from tests.fakes import FakeGroq, api_error, message, scripted


def vision(handler):
    s = Settings(_env_file=None, vision_backoff_base_s=0, vision_backoff_max_s=0,
                 vision_max_retries=1)
    fake = FakeGroq(handler)
    return VisionClient(fake, s), fake


def test_rotate_is_counter_clockwise_and_expands():
    img = Image.new("L", (100, 200), 255)
    img.putpixel((0, 0), 0)  # top-left marker
    out = rotate(img, 90)
    assert out.size == (200, 100)
    assert out.getpixel((0, 99)) == 0  # top-left moves to bottom-left when turned CCW
    assert rotate(img, 0) is img
    assert rotate(img, 180).size == (100, 200)


def test_mosaic_is_square_jpeg_with_four_tiles():
    data = build_mosaic(Image.new("L", (400, 600), 255), tile=320)
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    assert img.size == (640, 640)


@pytest.mark.parametrize(("tile", "degrees"), [(1, 0), (2, 90), (3, 180), (4, 270)])
async def test_detect_rotation_maps_tile_to_degrees(tile, degrees):
    client, fake = vision(scripted(message({"upright_tile": tile}, input_tokens=1355)))
    rotation, usage = await detect_rotation(Image.new("L", (300, 400), 255), vision=client)
    assert rotation == degrees
    assert usage.input_tokens == 1355
    assert len(fake.completions.calls) == 1


async def test_detect_rotation_falls_back_to_zero_on_failure():
    client, _ = vision(lambda _r: api_error(groq.BadRequestError, 400))
    rotation, usage = await detect_rotation(Image.new("L", (300, 400), 255), vision=client)
    assert rotation == 0
    assert usage.api_calls == 0


async def test_detect_rotation_auth_error_is_fatal():
    client, _ = vision(lambda _r: api_error(groq.AuthenticationError, 401))
    with pytest.raises(VisionAuthError):
        await detect_rotation(Image.new("L", (300, 400), 255), vision=client)
