"""Test doubles for Azure Document Intelligence. Results are real SDK `AnalyzeResult` objects."""

import asyncio
import math
from collections.abc import Callable
from typing import Any

from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

PAGE_W, PAGE_H = 2400.0, 1700.0  # upright landscape page, in reading-frame pixels


class FakeResponse:
    def __init__(self, status: int, headers: dict | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self.reason = "reason"
        self.content_type = "application/json"

    def text(self, encoding: str | None = None) -> str:
        return ""


def http_error(status: int, message: str = "error", headers: dict | None = None):
    cls = ClientAuthenticationError if status == 401 else HttpResponseError
    return cls(message=message, response=FakeResponse(status, headers))


def _rotate(x: float, y: float, angle: float) -> tuple[float, float]:
    a = math.radians(angle)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def _to_image(x: float, y: float, angle: float) -> tuple[float, float]:
    """Reading-frame point -> page image coordinates for a page whose text is at `angle`.
    The upright page [0, PAGE_W] x [0, PAGE_H] maps exactly onto the (rotated) image."""
    corners = [_rotate(cx, cy, angle) for cx, cy in
               ((0, 0), (PAGE_W, 0), (PAGE_W, PAGE_H), (0, PAGE_H))]
    min_x, min_y = min(c[0] for c in corners), min(c[1] for c in corners)
    rx, ry = _rotate(x, y, angle)
    return rx - min_x, ry - min_y


def ocr_result(
    rows: list[list[tuple[float, str]] | tuple[float, list[tuple[float, str]]]],
    *,
    angle: float = 0.0,
    low_confidence: set[str] = frozenset(),
    row_height: float = 40.0,
    top: float = 300.0,
    gap: float = 70.0,
    shuffle: bool = False,
) -> AnalyzeResult:
    """Build an AnalyzeResult from rows of (x, text) fragments laid out top to bottom.

    A row may be given as (y, fragments) to place it explicitly. Words listed in
    `low_confidence` get confidence 0.2. `shuffle` reverses the line order, like OCR
    reading order putting margin labels last.
    """
    lines, words, content = [], [], ""
    y = top
    for row in rows:
        if isinstance(row, tuple):
            y, fragments = row
        else:
            fragments = row
        for x, text in fragments:
            width = 18.0 * max(len(text), 1)
            corners = [(x, y), (x + width, y), (x + width, y + row_height), (x, y + row_height)]
            polygon = [c for px, py in corners for c in _to_image(px, py, angle)]
            offset = len(content)
            lines.append({"content": text, "polygon": polygon,
                          "spans": [{"offset": offset, "length": len(text)}]})
            pos = offset
            for word in text.split(" "):
                if word:
                    words.append({"content": word, "polygon": polygon,
                                  "confidence": 0.2 if word in low_confidence else 0.98,
                                  "span": {"offset": pos, "length": len(word)}})
                pos += len(word) + 1
            content += text + "\n"
        y += gap
    if shuffle:
        lines.reverse()
    # Page size in image coordinates (swap for sideways pages).
    w, h = (PAGE_H, PAGE_W) if round(angle / 90) % 2 else (PAGE_W, PAGE_H)
    return AnalyzeResult({
        "apiVersion": "2024-11-30", "modelId": "prebuilt-read", "content": content,
        "pages": [{"pageNumber": 1, "angle": angle, "width": w, "height": h, "unit": "pixel",
                   "spans": [], "words": words, "lines": lines}],
    })


class FakePoller:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def result(self) -> Any:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class FakeAzure:
    """`handler(body_bytes, call_index)` returns an AnalyzeResult or an exception.
    Exceptions raised at submit time (begin_analyze_document) when `fail_on_submit`."""

    def __init__(self, handler: Callable[[bytes, int], Any], delay: float = 0.0,
                 fail_on_submit: bool = True) -> None:
        self._handler = handler
        self._delay = delay
        self._fail_on_submit = fail_on_submit
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def begin_analyze_document(self, model_id: str, body: Any, **kwargs: Any) -> FakePoller:
        data = body.read()
        now = asyncio.get_running_loop().time()
        self.calls.append({"model_id": model_id, "body": data, "t": now})
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            outcome = self._handler(data, len(self.calls))
        finally:
            self.in_flight -= 1
        if isinstance(outcome, BaseException) and self._fail_on_submit:
            raise outcome
        return FakePoller(outcome)


def scripted(*outcomes: Any) -> Callable[[bytes, int], Any]:
    queue = list(outcomes)
    return lambda _body, _i: queue.pop(0)
