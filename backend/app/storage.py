"""Files on disk: uploaded PDFs, scheme files and page images (all student data).

Paths use database IDs only, never uploaded file names (they may contain student names).
Files are served only through authenticated endpoints.
"""

import asyncio
import shutil
from pathlib import Path

from fastapi import UploadFile

CHUNK = 1024 * 1024
PDF_MAGIC = b"%PDF-"
XLSX_MAGIC = b"PK\x03\x04"  # .xlsx files are zip archives


class UploadError(Exception):
    pass


def script_dir(storage: Path, script_id: int) -> Path:
    return storage / "scripts" / str(script_id)


def script_pdf(storage: Path, script_id: int) -> Path:
    return script_dir(storage, script_id) / "original.pdf"


def page_image(storage: Path, script_id: int, page: int, *, thumb: bool = False) -> Path:
    name = f"thumb_{page}.jpg" if thumb else f"page_{page}.jpg"
    return script_dir(storage, script_id) / "pages" / name


def exam_scheme(storage: Path, exam_id: int) -> Path:
    return storage / "exams" / str(exam_id) / "scheme.xlsx"


async def save_upload(upload: UploadFile, dest: Path, *, max_bytes: int, magic: bytes) -> int:
    """Stream an upload to `dest`, checking its type by content and its size. Disk writes
    run in a thread so a large upload does not block other requests."""
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    out = await asyncio.to_thread(dest.open, "wb")
    size = 0
    first = True
    try:
        while chunk := await upload.read(CHUNK):
            if first:
                if not chunk.startswith(magic):
                    raise UploadError("wrong file type")
                first = False
            size += len(chunk)
            if size > max_bytes:
                raise UploadError(f"larger than {max_bytes // CHUNK} MB")
            await asyncio.to_thread(out.write, chunk)
        if first:
            raise UploadError("empty file")
    except UploadError:
        await asyncio.to_thread(out.close)
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        raise
    await asyncio.to_thread(out.close)
    return size


async def copy_file(src: Path, dest: Path) -> None:
    def _copy() -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    await asyncio.to_thread(_copy)
