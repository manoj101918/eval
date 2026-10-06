"""Load one answer script (a PDF, image files, or a directory of images) as grayscale pages."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
PDF_SUFFIX = ".pdf"


class IngestError(Exception):
    """Input could not be loaded. Messages never include file names (they may hold roll numbers)."""


@dataclass(frozen=True)
class RawPage:
    index: int  # 0-based position in the script
    image: Image.Image  # full-resolution grayscale ("L")


def _natural_key(path: Path) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def _expand(paths: Sequence[Path]) -> list[Path]:
    if not paths:
        raise IngestError("No input files given.")
    files: list[Path] = []
    for i, p in enumerate(paths, start=1):
        if p.is_dir():
            found = sorted(
                (f for f in p.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES), key=_natural_key
            )
            if not found:
                raise IngestError(f"Input {i} is a directory with no supported images.")
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            raise IngestError(f"Input {i} does not exist.")
    return files


def _load_pdf(path: Path, dpi: int) -> list[Image.Image]:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises several types for corrupt files
        raise IngestError("The PDF could not be opened (corrupt or not a PDF).") from exc
    with doc:
        if doc.needs_pass:
            raise IngestError("The PDF is password-protected.")
        if doc.page_count == 0:
            raise IngestError("The PDF has no pages.")
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            images.append(Image.frombytes("L", (pix.width, pix.height), pix.samples))
        return images


def _load_image(path: Path, position: int) -> list[Image.Image]:
    try:
        with Image.open(path) as img:
            # Multi-page TIFFs yield several frames; other formats yield one.
            frames = ImageSequence.Iterator(img)
            return [ImageOps.exif_transpose(frame).convert("L") for frame in frames]
    except (UnidentifiedImageError, OSError) as exc:
        raise IngestError(f"Input file {position} is not a readable image.") from exc


def load_script(paths: Sequence[Path], *, dpi: int = 200) -> list[RawPage]:
    """Load all pages of one answer script, in order, as full-resolution grayscale images."""
    files = _expand([Path(p) for p in paths])
    suffixes = [f.suffix.lower() for f in files]

    if PDF_SUFFIX in suffixes:
        if len(files) != 1:
            raise IngestError("Give either one PDF or a set of images for a script, not both.")
        images = _load_pdf(files[0], dpi)
    else:
        images = []
        for position, (f, suffix) in enumerate(zip(files, suffixes, strict=True), start=1):
            if suffix not in IMAGE_SUFFIXES:
                raise IngestError(f"Input file {position} has an unsupported type '{suffix}'.")
            images.extend(_load_image(f, position))

    return [RawPage(index=i, image=img) for i, img in enumerate(images)]
