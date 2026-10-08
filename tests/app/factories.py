"""Test files: marking-scheme workbooks and answer-script PDFs."""

import io

from openpyxl import Workbook

from tests import pages

SCHEME_HEADER = ["Question", "Max marks", "Type", "Correct option", "Model answer / key points",
                 "Marking guidance", "Choice group"]
SCHEME_ROWS = [
    ["1", 1, "mcq", "c", None, None, None],
    ["5", 3, "long", None, "three key points", "1 mark each", None],
]


def scheme_xlsx(rows=SCHEME_ROWS) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"
    ws.append(SCHEME_HEADER)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def script_pdf(roll: str | None = "21CS045") -> bytes:
    """A two-page answer script: cover (with a QR roll number) and one written page."""
    cover = pages.cover_page(roll)
    written = pages.written_page(5)
    buf = io.BytesIO()
    cover.save(buf, format="PDF", save_all=True, append_images=[written], resolution=200)
    return buf.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def create_exam(client, scheme: bytes | None = None, **fields):
    data = {"name": "Mid 1", "subject": "Physics", "class_section": "XII-A"} | fields
    return await client.post("/api/admin/exams", data=data,
                             files={"scheme": ("scheme.xlsx", scheme or scheme_xlsx(), XLSX)})
