"""CLI: python -m backend.extract samples/script1.pdf [--pretty]

Prints the ExtractionResult JSON to stdout and the total time to stderr.
Exit codes: 0 ok, 1 input error, 2 some pages failed (JSON still printed).
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from backend.config import Settings, get_settings
from backend.extract.ingest import IngestError
from backend.extract.pipeline import extract_script
from backend.extract.schemas import ExtractionResult
from backend.extract.vision import VisionAuthError, VisionClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.extract",
        description="Transcribe one handwritten answer script into structured JSON.",
    )
    parser.add_argument("paths", nargs="+", type=Path,
                        help="one PDF, several page images, or a directory of images")
    parser.add_argument("--concurrency", type=int, help="max parallel vision calls")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    return parser


async def _run(paths: list[Path], settings: Settings, vision: VisionClient) -> ExtractionResult:
    try:
        return await extract_script(paths, settings=settings, vision=vision)
    finally:
        await vision.aclose()


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    vision_factory: Callable[[Settings], VisionClient] = VisionClient.from_settings,
) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # transcriptions may hold non-ASCII text

    settings = settings or get_settings()
    if args.concurrency:
        settings = settings.model_copy(update={"vision_max_concurrency": args.concurrency})

    start = time.perf_counter()
    try:
        vision = vision_factory(settings)
        result = asyncio.run(_run(args.paths, settings, vision))
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except VisionAuthError as exc:
        print(f"error: {exc} Set GROQ_API_KEY in .env.", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - start

    print(result.model_dump_json(indent=2 if args.pretty else None))
    print(
        f"Total time: {elapsed:.1f}s ({result.pages_total} pages, "
        f"{len(result.blank_pages)} blank, {len(result.failed_pages)} failed, "
        f"{result.usage.api_calls} API calls)",
        file=sys.stderr,
    )
    if vision.daily_limit_reached:
        print(
            "note: the Groq daily limit was reached, so some pages were not sent. "
            "Rerun later, or raise the account's limits.",
            file=sys.stderr,
        )
    return 2 if result.failed_pages else 0


if __name__ == "__main__":
    sys.exit(main())
