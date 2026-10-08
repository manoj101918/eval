"""CLI: grade one answer script against an Excel marking scheme (proposed marks only).

    python -m backend.grading --scheme scheme.xlsx --script result.json
    python -m backend.grading --scheme scheme.xlsx samples/script1.pdf

Prints the GradingResult JSON to stdout and a summary to stderr.
Exit codes: 0 ok, 1 input/scheme/key error, 2 some questions could not be graded.
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from backend.cli_utils import emit_json
from backend.config import Settings, get_settings
from backend.extract.clients import TranscriptionClient, make_client
from backend.extract.ingest import IngestError
from backend.extract.pipeline import extract_script
from backend.extract.schemas import ExtractionResult
from backend.grading.grader import Grader
from backend.grading.schemas import GradingResult
from backend.grading.scheme import MarkingScheme, SchemeError, load_scheme
from backend.llm.groq_chat import VisionAuthError
from backend.logging_setup import cap_library_loggers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.grading",
        description="Propose marks for one answer script using an Excel marking scheme.",
    )
    parser.add_argument("--scheme", type=Path, required=True, help="marking scheme .xlsx")
    parser.add_argument("--script", type=Path,
                        help="extraction result JSON (from python -m backend.extract)")
    parser.add_argument("paths", nargs="*", type=Path,
                        help="or the scan itself: one PDF, page images, or a directory")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument("-o", "--output", type=Path,
                        help="write the JSON to this file (UTF-8) instead of the screen")
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    return parser


async def _run(
    scheme: MarkingScheme, extraction: ExtractionResult | None, paths: list[Path],
    settings: Settings, grader: Grader, extract_factory: Callable[[Settings], TranscriptionClient],
) -> GradingResult:
    try:
        if extraction is None:
            vision = extract_factory(settings)
            try:
                extraction = await extract_script(paths, settings=settings, vision=vision)
            finally:
                await vision.aclose()
        return await grader.grade(extraction, scheme)
    finally:
        await grader.aclose()


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    grader_factory: Callable[[Settings], Grader] = Grader.from_settings,
    extract_factory: Callable[[Settings], TranscriptionClient] = make_client,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.script) == bool(args.paths):
        parser.error("give either --script result.json or the scan path(s), not both")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cap_library_loggers()  # library DEBUG output can carry student data
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings or get_settings()

    try:
        scheme = load_scheme(args.scheme)
    except SchemeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    extraction = None
    if args.script:
        try:
            extraction = ExtractionResult.model_validate_json(
                args.script.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            print(f"error: cannot read the extraction result ({type(exc).__name__})",
                  file=sys.stderr)
            return 1

    start = time.perf_counter()
    try:
        grader = grader_factory(settings)
        result = asyncio.run(_run(scheme, extraction, args.paths, settings, grader,
                                  extract_factory))
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except VisionAuthError as exc:
        print(f"error: {exc} Check the API keys in .env.", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - start

    emit_json(result.model_dump_json(indent=2 if args.pretty else None), args.output)
    print(
        f"Proposed total: {result.total_marks:g} / {result.max_marks:g} "
        f"({result.needs_review} to review, {len(result.failed)} failed, "
        f"{len(result.unmatched_answers)} unmatched answers) in {elapsed:.1f}s, "
        f"{result.usage.api_calls} grading calls. Not final until a teacher approves.",
        file=sys.stderr,
    )
    if grader.daily_limit_reached:
        print("note: the Groq daily limit was reached; ungraded questions are listed as "
              "failed. Rerun later.", file=sys.stderr)
    return 2 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
