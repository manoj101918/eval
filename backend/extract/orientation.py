"""Page orientation: detect how a script was scanned (sideways / upside down) and rotate upright.

Detection uses the vision model once per script: one image shows a sample page at all four
rotations in numbered tiles, and the model picks the upright tile. Pixel heuristics proved
unreliable on real scans (faint rulings, scanner borders, landscape booklets).
"""

import io
import logging

from PIL import Image, ImageDraw, ImageFont

from backend.extract.schemas import Usage
from backend.extract.vision import PAGE_FAILURES, VisionAuthError, VisionClient

logger = logging.getLogger(__name__)

ROTATIONS = (0, 90, 180, 270)  # degrees counter-clockwise, for tiles 1..4
TILE_PX = 640


def rotate(img: Image.Image, degrees: int) -> Image.Image:
    """Rotate counter-clockwise by a multiple of 90 degrees (lossless, canvas expands)."""
    return img.rotate(degrees, expand=True) if degrees % 360 else img


def build_mosaic(img: Image.Image, *, tile: int = TILE_PX, quality: int = 80) -> bytes:
    """2x2 JPEG with the page at each rotation in ROTATIONS, tiles labelled 1-4."""
    canvas = Image.new("L", (tile * 2, tile * 2), 255)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=64)
    for i, degrees in enumerate(ROTATIONS):
        t = rotate(img.convert("L"), degrees)
        t.thumbnail((tile - 20, tile - 20))
        x, y = (i % 2) * tile, (i // 2) * tile
        canvas.paste(t, (x + (tile - t.width) // 2, y + (tile - t.height) // 2))
        draw.rectangle([x + 4, y + 4, x + 90, y + 84], fill=0)
        draw.text((x + 26, y + 8), str(i + 1), fill=255, font=font)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


async def detect_rotation(sample: Image.Image, *, vision: VisionClient) -> tuple[int, Usage]:
    """Degrees (counter-clockwise) that make `sample` upright. Falls back to 0 on failure."""
    try:
        result = await vision.check_orientation(build_mosaic(sample))
    except VisionAuthError:
        raise
    except PAGE_FAILURES as exc:
        logger.warning("orientation check failed (%s); assuming pages are upright",
                       type(exc).__name__)
        return 0, Usage()
    degrees = ROTATIONS[result.data.upright_tile - 1]
    logger.info("orientation check: rotating pages %d degrees", degrees)
    return degrees, result.usage
