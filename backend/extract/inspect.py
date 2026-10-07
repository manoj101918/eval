"""Offline page inspector for tuning blank detection (no API calls).

    python -m backend.extract.inspect samples/script1.pdf [--threshold 0.0001]

Prints only page sizes, ink ratios and verdicts - never page content or roll numbers.
"""

import argparse
import sys
from pathlib import Path

from backend.config import get_settings
from backend.extract.ingest import IngestError, load_script
from backend.extract.preprocess import preprocess_page
from backend.extract.roll_number import decode_qr, match_roll_number


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(prog="python -m backend.extract.inspect")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--threshold", type=float, default=s.blank_ink_ratio,
                        help=f"blank ink-ratio threshold (default {s.blank_ink_ratio})")
    args = parser.parse_args(argv)

    try:
        raw_pages = load_script(args.paths, dpi=s.pdf_render_dpi)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    header = ("page", "source px", "sent px", "jpeg KB", "ink ratio")
    print("{:>4}  {:>11}  {:>9}  {:>7}  {:>9}  verdict".format(*header))
    for raw in raw_pages:
        page = preprocess_page(raw, max_px=s.image_max_px, jpeg_quality=s.jpeg_quality,
                               blank_ink_ratio=args.threshold)
        is_cover = s.has_cover_page and raw.index == 0
        if is_cover:
            payloads = decode_qr(raw.image)
            valid = any(match_roll_number(p, s.roll_number_pattern) for p in payloads)
            verdict = "cover, " + (
                "QR with valid roll number" if valid
                else "QR found but no valid roll number" if payloads
                else "no QR (vision fallback)"
            )
        else:
            verdict = "BLANK - skipped" if page.blank else "sent"
        src = f"{raw.image.width}x{raw.image.height}"
        sent = f"{page.width}x{page.height}"
        print(f"{raw.index + 1:>4}  {src:>11}  {sent:>9}  {len(page.jpeg) / 1024:>7.0f}  "
              f"{page.ink_ratio:>9.5f}  {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
