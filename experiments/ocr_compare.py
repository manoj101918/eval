"""Compare transcription engines on sample pages against hand-made reference text.

    python -m experiments.ocr_compare [--engines groq,easyocr,azure] [--pages 2,5,20]

Inputs (git-ignored, in samples/ocr_test/): pageN.jpg (upright, as the pipeline sends it),
pageN_full.png (upright, full resolution, for OCR engines) and reference_pageN.txt.
Outputs: <engine>_pageN.txt next to them, and results.md. Only scores are printed.
"""

import argparse
import asyncio
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

DIR = Path("samples/ocr_test")
SYMBOLS = set("ΩβλμΔ∝∴√π°")  # characters an OCR engine cannot spell in plain ASCII
_DIAGRAM_NOTE = re.compile(r"\[(diagram|figure|graph|circuit)[^\]]*\]", re.IGNORECASE)
# Equivalent notations are not errors: "⇒" vs "=>", "×" vs "x", "R₁" vs "R1", "R_eff" vs "Reff".
_EQUIVALENTS = str.maketrans({
    "⇒": "=>", "→": "->", "×": "x", "−": "-", "–": "-", "’": "'", "‘": "'",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "_": "",
})


# --- metrics -----------------------------------------------------------------------


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_EQUIVALENTS).lower()).strip()


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    return edit_distance(ref, hyp) / max(len(ref), 1)


def word_recall(reference: str, hypothesis: str) -> float:
    ref = Counter(re.findall(r"[a-z]{3,}", normalize(reference)))
    hyp = Counter(re.findall(r"[a-z]{3,}", normalize(hypothesis)))
    total = sum(ref.values())
    return sum(min(n, hyp[w]) for w, n in ref.items()) / max(total, 1)


def symbol_recall(reference: str, hypothesis: str) -> tuple[int, int]:
    ref = Counter(c for c in reference if c in SYMBOLS)
    hyp = Counter(c for c in hypothesis if c in SYMBOLS)
    return sum(min(n, hyp[c]) for c, n in ref.items()), sum(ref.values())


# --- engines -------------------------------------------------------------------------


async def run_groq(page: int) -> str:
    from backend.config import Settings
    from backend.extract.vision import VisionClient

    client = VisionClient.from_settings(Settings())
    try:
        result = await client.transcribe_page((DIR / f"page{page}.jpg").read_bytes(),
                                              page_number=page)
    finally:
        await client.aclose()
    parts = []
    for seg in result.data.segments:
        text = _DIAGRAM_NOTE.sub("", seg.text).strip()
        parts.append(f"{seg.question_number} {text}" if seg.question_number else text)
    return "\n".join(parts)


_easyocr_reader = None


def run_easyocr(page: int) -> str:
    global _easyocr_reader
    import easyocr

    if _easyocr_reader is None:
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    lines = _easyocr_reader.readtext(str(DIR / f"page{page}_full.png"), detail=0,
                                     paragraph=True)
    return "\n".join(lines)


def run_azure(page: int) -> str:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    env = dotenv_values(".env")
    client = DocumentIntelligenceClient(env["AZURE_DI_ENDPOINT"],
                                        AzureKeyCredential(env["AZURE_DI_KEY"]))
    with open(DIR / f"page{page}_full.png", "rb") as f:
        poller = client.begin_analyze_document("prebuilt-read", body=f)
    return poller.result().content


def azure_configured() -> bool:
    env = dotenv_values(".env")
    return bool(env.get("AZURE_DI_ENDPOINT") and env.get("AZURE_DI_KEY"))


async def transcribe(engine: str, page: int) -> str:
    if engine == "groq":
        return await run_groq(page)
    fn = {"easyocr": run_easyocr, "azure": run_azure}[engine]
    return await asyncio.to_thread(fn, page)


# --- main ----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m experiments.ocr_compare")
    parser.add_argument("--engines", default="groq,easyocr,azure")
    parser.add_argument("--pages", default="2,5,20")
    parser.add_argument("--rescore", action="store_true",
                        help="score existing outputs instead of calling the engines again")
    args = parser.parse_args()
    engines = [e.strip() for e in args.engines.split(",")]
    pages = [int(p) for p in args.pages.split(",")]
    if "azure" in engines and not args.rescore and not azure_configured():
        print("azure: skipped (AZURE_DI_ENDPOINT / AZURE_DI_KEY not set in .env)")
        engines.remove("azure")

    rows = []
    for engine in engines:
        for page in pages:
            out_path = DIR / f"{engine}_page{page}.txt"
            if args.rescore:
                if not out_path.exists():
                    continue
                text, seconds = out_path.read_text(encoding="utf-8"), float("nan")
            else:
                start = time.perf_counter()
                try:
                    text = await transcribe(engine, page)
                except Exception as exc:  # report and continue with other engines
                    print(f"{engine} page {page}: failed ({type(exc).__name__})")
                    continue
                seconds = time.perf_counter() - start
                out_path.write_text(text, encoding="utf-8")
            ref = (DIR / f"reference_page{page}.txt").read_text(encoding="utf-8")
            kept, total = symbol_recall(ref, text)
            rows.append((engine, page, word_recall(ref, text), cer(ref, text), kept, total,
                         seconds))

    lines = [
        "| Engine | Page | Word recall (higher better) | Char error rate (lower better) "
        "| Math symbols kept | Seconds |",
        "|---|---|---|---|---|---|",
    ]
    for engine, page, wr, ce, kept, total, seconds in rows:
        lines.append(f"| {engine} | {page} | {wr:.0%} | {ce:.0%} | {kept}/{total} "
                     f"| {seconds:.1f} |")
    for engine in engines:
        mine = [r for r in rows if r[0] == engine]
        if mine:
            lines.append(
                f"| **{engine} avg** | | **{sum(r[2] for r in mine) / len(mine):.0%}** "
                f"| **{sum(r[3] for r in mine) / len(mine):.0%}** "
                f"| {sum(r[4] for r in mine)}/{sum(r[5] for r in mine)} "
                f"| {sum(r[6] for r in mine) / len(mine):.1f} |"
            )
    report = "\n".join(lines)
    (DIR / "results.md").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
