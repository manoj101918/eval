"""Read the student's roll number from the cover page: QR code first, vision model as fallback."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import cv2
import numpy as np
from PIL import Image

from backend.extract.schemas import Usage

if TYPE_CHECKING:
    from backend.extract.clients import TranscriptionClient

logger = logging.getLogger(__name__)


@dataclass
class RollNumberResult:
    value: str | None
    source: Literal["qr", "vision", "ocr"] | None
    usage: Usage = field(default_factory=Usage)


def decode_qr(gray: Image.Image) -> list[str]:
    """Decode every QR code on the page. Run on the full-resolution image."""
    ok, payloads, _, _ = cv2.QRCodeDetector().detectAndDecodeMulti(np.asarray(gray.convert("L")))
    return [p for p in payloads if p] if ok else []


def match_roll_number(text: str | None, pattern: str) -> str | None:
    """Extract a roll number from free text (QR payload or model output).

    Prefers a single token that matches `pattern` and contains a digit (e.g. 'ROLL:21CS045').
    Several such tokens are ambiguous and give None. With no matching token, the whole text
    with separators removed is tried (e.g. '21-CS-045').
    """
    if not text:
        return None
    upper = text.upper()

    def valid(s: str) -> bool:
        return bool(re.fullmatch(pattern, s)) and any(c.isdigit() for c in s)

    candidates = {t for t in re.findall(r"[A-Z0-9]+", upper) if valid(t)}
    if len(candidates) == 1:
        return candidates.pop()
    if candidates:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", upper)
    return compact if valid(compact) else None


async def read_roll_number(
    cover_full_res: Image.Image,
    cover_jpeg: bytes,
    *,
    vision: "TranscriptionClient",
    pattern: str,
) -> RollNumberResult:
    for payload in await asyncio.to_thread(decode_qr, cover_full_res):
        value = match_roll_number(payload, pattern)
        if value:
            logger.info("roll number read from QR code")
            return RollNumberResult(value, "qr")

    try:
        result = await vision.read_cover(cover_jpeg)
    except Exception as exc:  # never fail the whole script over the roll number
        logger.warning("cover page vision call failed (%s); roll number left empty",
                       type(exc).__name__)
        return RollNumberResult(None, None)

    value = match_roll_number(result.data.roll_number, pattern)
    if value:
        source = getattr(vision, "roll_number_source", "vision")
        logger.info("roll number read from cover page by %s", source)
        return RollNumberResult(value, source, result.usage)
    logger.warning("no valid roll number found on cover page; teacher must enter it")
    return RollNumberResult(None, None, result.usage)
