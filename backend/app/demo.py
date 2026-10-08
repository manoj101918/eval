"""Demo server: the real API and grading pipeline with FAKE AI responses.

    python -m backend.app.demo --yes-demo [--port 8000] [--reset]

For frontend development and browser tests without Azure/Groq keys or quota. It uses its
own SQLite database and files under data/demo/ (never DATABASE_URL from .env), seeds demo
accounts, and writes a sample marking scheme and answer-script PDF to upload.
Marks it produces are fake: never use it for real scripts.
"""

import argparse
import asyncio
import io
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from groq.types.chat import ChatCompletion
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from backend.app import auth
from backend.app.db import Base, make_engine, make_sessionmaker
from backend.app.main import create_app
from backend.app.models import User
from backend.config import Settings
from backend.extract.vision import VisionClient
from backend.grading.grader import Grader

DEMO_ROOT = Path("data/demo")
DEMO_USERS = [  # employee_id, name, role, password
    ("ADM1", "Exam Cell (demo)", "admin", "demo-admin-1"),
    ("T100", "Asha (demo)", "teacher", "demo-teacher-1"),
    ("T200", "Ravi (demo)", "teacher", "demo-teacher-2"),
]
SCHEME = [
    ["1", 1, "mcq", "c", None, None, None],
    ["2", 2, "short", None, "Ohm's law: V = IR, current proportional to voltage at constant "
     "temperature.", "1 mark statement, 1 mark formula", None],
    ["3", 3, "long", None, "Newton's three laws of motion: inertia, F = ma, action-reaction.",
     "1 mark per law", None],
]
PAGE_TEXT = {
    2: [("1", "(c)"), ("2", "Ohm's law says V = IR when temperature stays constant.")],
    3: [("3", "First law: a body stays at rest unless a force acts. Second law: F = ma.")],
}


# --- fake AI ----------------------------------------------------------------------------


def _completion(payload: dict, prompt_tokens: int = 900) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "demo", "object": "chat.completion", "created": 0, "model": "demo",
        "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                     "message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 120,
                  "total_tokens": prompt_tokens + 120},
    })


def _user_text(request: dict[str, Any]) -> str:
    content = request["messages"][-1]["content"]
    return content if isinstance(content, str) else content[-1]["text"]


class _Completions:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def create(self, **request: Any) -> ChatCompletion:
        await asyncio.sleep(0.05)
        text = _user_text(request)
        if self.kind == "grade":
            maximum = float(re.search(r"maximum ([\d.]+) marks", text).group(1))
            if maximum >= 3:  # a deduction the teacher has to check
                return _completion({"marks_awarded": maximum - 1, "points_met": ["most points"],
                                    "points_missing": ["one point"], "confidence": "medium",
                                    "reason": "Demo: one point missing.", "ocr_problem": False})
            return _completion({"marks_awarded": maximum, "points_met": ["all points"],
                                "points_missing": [], "confidence": "high",
                                "reason": "Demo: complete answer.", "ocr_problem": False})
        page = re.search(r"page (\d+) of the script", text)
        if page is None:  # cover page: no readable roll number, the teacher enters it
            return _completion({"roll_number": None})
        segments = [{"question_number": q, "text": t, "has_diagram": False, "illegible": False,
                     "illegible_notes": []} for q, t in PAGE_TEXT.get(int(page.group(1)), [])]
        return _completion({"segments": segments})


class DemoGroq:
    """Stands in for groq.AsyncGroq: same call shape, canned answers, no network."""

    def __init__(self, kind: str) -> None:
        self.chat = SimpleNamespace(completions=_Completions(kind))

    async def close(self) -> None:
        return None


# --- settings, seed data, sample files ----------------------------------------------------


def demo_settings(root: Path = DEMO_ROOT) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(root / 'demo.db').as_posix()}",
        storage_dir=root / "storage", export_dir=root / "exports", excel_master_path=None,
        transcribe_provider="groq", page_rotation="0",
        vision_tokens_per_minute=0, grading_tokens_per_minute=0,
        vision_backoff_base_s=0, vision_backoff_max_s=0,
    )


def build_demo_app(settings: Settings) -> FastAPI:
    return create_app(
        settings,
        extraction_factory=lambda s: VisionClient(DemoGroq("vision"), s),
        grader_factory=lambda s: Grader(DemoGroq("grade"), s),
    )


async def seed(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with make_sessionmaker(engine)() as db:
        for employee_id, name, role, password in DEMO_USERS:
            if not await db.scalar(select(User).where(User.employee_id == employee_id)):
                db.add(User(employee_id=employee_id, name=name, role=role,
                            password_hash=auth.hash_password(password),
                            must_change_password=False))
        await db.commit()
    await engine.dispose()


def sample_scheme() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"
    ws.append(["Question", "Max marks", "Type", "Correct option", "Model answer / key points",
               "Marking guidance", "Choice group"])
    for row in SCHEME:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sample_pdf() -> bytes:
    """Cover page plus two written pages (enough ink not to count as blank)."""
    font = ImageFont.load_default(size=36)
    pages = []
    for n in (1, 2, 3):
        page = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(page)
        lines = ["ANSWER BOOKLET (demo)", "Roll No: ______"] if n == 1 else \
            [f"{q}. {t}" for q, t in PAGE_TEXT[n]] * 6
        for i, line in enumerate(lines):
            draw.text((100, 150 + i * 70), line[:60], fill=0, font=font)
        pages.append(page)
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


def write_samples(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample_scheme.xlsx").write_bytes(sample_scheme())
    (root / "sample_script.pdf").write_bytes(sample_pdf())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.demo")
    parser.add_argument("--yes-demo", action="store_true",
                        help="confirm: run with fake AI and demo data")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reset", action="store_true", help="delete the demo data first")
    parser.add_argument("--root", type=Path, default=DEMO_ROOT)
    args = parser.parse_args(argv)
    if not args.yes_demo:
        print("error: this starts a DEMO server with fake AI marks. Add --yes-demo to confirm.",
              file=sys.stderr)
        return 2
    if args.reset:
        shutil.rmtree(args.root, ignore_errors=True)
    settings = demo_settings(args.root)
    write_samples(args.root)
    asyncio.run(seed(settings))
    print(f"Demo server on http://127.0.0.1:{args.port}  (fake AI marks)")
    for employee_id, _, role, password in DEMO_USERS:
        print(f"  {role:<8} {employee_id}  password {password}")
    print(f"  sample files: {args.root / 'sample_scheme.xlsx'}, {args.root / 'sample_script.pdf'}")

    import uvicorn

    uvicorn.run(build_demo_app(settings), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
