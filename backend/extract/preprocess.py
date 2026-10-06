"""Page preprocessing: downscale, JPEG-compress and blank-page detection."""

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from backend.extract.ingest import RawPage

BORDER_CROP = 0.05  # ignore scanner edges, staples and punch holes
INK_DELTA = 60  # a pixel is ink if it is this much darker than the paper (median)
MIN_SPECK_AREA = 12  # connected ink blobs smaller than this (px) are scanner noise
LINE_FRACTION = 8  # lines longer than 1/LINE_FRACTION of the page are printed rulings
RULING_COVERAGE = 0.5  # a pixel row/column more than half covered by ink is a ruling line
SLIVER_MAX_THICKNESS = 4  # px; thin elongated blobs are leftover ruling, not writing


@dataclass(frozen=True)
class PageImage:
    index: int  # 0-based position in the script
    jpeg: bytes
    width: int
    height: int
    ink_ratio: float
    blank: bool


def downscale(img: Image.Image, max_px: int) -> Image.Image:
    """Shrink so the longest side is at most `max_px`. Never upscales."""
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return out


def to_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def ink_ratio(gray: Image.Image) -> float:
    """Fraction of the page covered by handwriting-like ink.

    Printed ruling lines (long horizontal/vertical lines) and isolated scanner
    specks are removed first, so a blank ruled page scores close to zero.
    """
    arr = np.asarray(gray.convert("L"), dtype=np.uint8)
    h, w = arr.shape
    dy, dx = int(h * BORDER_CROP), int(w * BORDER_CROP)
    arr = arr[dy : h - dy, dx : w - dx]
    if arr.size == 0:
        return 0.0
    h, w = arr.shape

    arr = cv2.medianBlur(arr, 3)
    paper = float(np.median(arr))
    mask = (arr < paper - INK_DELTA).astype(np.uint8) * 255

    # Remove printed rulings. (1) Rows/columns mostly covered by ink are ruling lines, even
    # when downscaling has broken them into dashes. (2) Solid long segments (table borders,
    # partial lines) are caught by a morphological opening with a long kernel.
    ink = mask > 0
    rows = ink.mean(axis=1) > RULING_COVERAGE
    cols = ink.mean(axis=0) > RULING_COVERAGE
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // LINE_FRACTION), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // LINE_FRACTION)))
    lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, h_kernel) | cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, v_kernel
    )
    lines[rows, :] = 255
    lines[:, cols] = 255
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))  # also catch anti-aliased line edges
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(lines))

    # Drop tiny specks and thin slivers (short ruling stubs, e.g. left of the margin line).
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return 0.0
    bw, bh = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]
    thin = np.minimum(bw, bh) <= SLIVER_MAX_THICKNESS
    elongated = np.maximum(bw, bh) >= 4 * np.minimum(bw, bh)
    keep = (stats[:, cv2.CC_STAT_AREA] >= MIN_SPECK_AREA) & ~(thin & elongated)
    keep[0] = False  # background
    return float(np.count_nonzero(keep[labels])) / mask.size


def preprocess_page(
    raw: RawPage, *, max_px: int, jpeg_quality: int, blank_ink_ratio: float
) -> PageImage:
    small = downscale(raw.image, max_px)
    ratio = ink_ratio(small)
    return PageImage(
        index=raw.index,
        jpeg=to_jpeg(small, jpeg_quality),
        width=small.width,
        height=small.height,
        ink_ratio=ratio,
        blank=ratio < blank_ink_ratio,
    )
