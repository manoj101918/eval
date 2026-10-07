"""Helpers shared by the command-line tools."""

from pathlib import Path


def emit_json(text: str, output: Path | None) -> None:
    """Print JSON, or write it as plain UTF-8 (shell redirects on Windows can add a BOM or
    use UTF-16)."""
    if output is None:
        print(text)
    else:
        output.write_text(text + "\n", encoding="utf-8")
