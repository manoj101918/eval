"""Synthetic answer-booklet pages for tests (A4 at 200 dpi, grayscale)."""

import cv2
import numpy as np
import qrcode
from PIL import Image

W, H = 1654, 2339


def _paper(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = np.full((H, W), 242, np.float32) + rng.normal(0, 4, (H, W))
    for _ in range(300):  # scanner dust
        y, x = rng.integers(0, H - 2), rng.integers(0, W - 2)
        a[y : y + 2, x : x + 2] = 60
    return a


def _rule(a: np.ndarray) -> np.ndarray:
    for y in range(250, H - 100, 62):
        a[y : y + 3, :] = 150
    a[:, 180:183] = 110  # margin line
    return a


def _to_image(a: np.ndarray) -> Image.Image:
    return Image.fromarray(a.clip(0, 255).astype(np.uint8), mode="L")


def _write(a: np.ndarray, lines: int, word: str = "Newton law", words: int = 6) -> np.ndarray:
    a = a.clip(0, 255).astype(np.uint8)
    for i in range(lines):
        text = " ".join([word] * words)[:60]
        cv2.putText(a, text, (200, 300 + i * 62), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.6, 40, 3,
                    cv2.LINE_AA)
    return a.astype(np.float32)


def blank_page() -> Image.Image:
    return _to_image(_paper())


def ruled_blank_page() -> Image.Image:
    return _to_image(_rule(_paper()))


def written_page(lines: int = 30) -> Image.Image:
    return _to_image(_write(_rule(_paper()), lines))


def short_answer_page() -> Image.Image:
    """A ruled page with only '42' written on it — must NOT count as blank."""
    return _to_image(_write(_rule(_paper()), 1, word="42", words=1))


def cover_page(qr_payload: str | None = None) -> Image.Image:
    a = _paper().clip(0, 255).astype(np.uint8)
    cv2.putText(a, "ANSWER BOOKLET", (450, 250), cv2.FONT_HERSHEY_SIMPLEX, 2.5, 20, 5)
    cv2.putText(a, "Roll No:", (200, 600), cv2.FONT_HERSHEY_SIMPLEX, 2, 20, 4)
    img = Image.fromarray(a, mode="L")
    if qr_payload is not None:
        qr = qrcode.make(qr_payload, box_size=8, border=4).get_image().convert("L")
        img.paste(qr, (W - qr.width - 150, 150))
    return img
