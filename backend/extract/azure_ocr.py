"""Azure Document Intelligence OCR client: bounded concurrency, per-call timeouts, retries,
shared pauses, and Pydantic validation of every page it returns.

The service handles page rotation itself and reports the text angle per page.
"""

import asyncio
import io
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from pydantic import ValidationError

from backend.config import Settings
from backend.extract.layout import find_roll_number, segment_pages
from backend.extract.retry import backoff_delay, with_retries
from backend.extract.schemas import CoverPageInfo, OcrPage, PageTranscription, Usage
from backend.extract.vision import (
    DailyLimitError,
    InvalidOutputError,
    VisionAuthError,
    VisionError,
    VisionResult,
)

logger = logging.getLogger(__name__)

RATE_LIMIT_MIN_PAUSE_S = 2.0
RETRYABLE_STATUS = {408, 409, 429}


class AzureTransientError(VisionError):
    retryable = True


class AzureRateLimitedError(VisionError):
    retryable = True
    rate_limited = True

    def __init__(self, message: str, retry_after_s: float | None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AnalyzeAPI(Protocol):
    async def begin_analyze_document(self, model_id: str, body: Any, **kwargs: Any) -> Any: ...


def _retry_after(exc: HttpResponseError) -> float | None:
    headers = getattr(exc.response, "headers", None) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def to_ocr_page(result: Any) -> OcrPage:
    """Validate the service's first page into our schema (raises InvalidOutputError)."""
    try:
        page = result.pages[0]
        return OcrPage.model_validate(
            {
                "angle": page.angle or 0.0,
                "width": page.width,
                "height": page.height,
                "lines": [
                    {"text": ln.content, "polygon": list(ln.polygon or []),
                     "offset": ln.spans[0].offset if ln.spans else 0,
                     "length": sum(s.length for s in ln.spans or [])}
                    for ln in page.lines or []
                ],
                "words": [
                    {"text": w.content, "confidence": w.confidence, "offset": w.span.offset}
                    for w in page.words or []
                ],
            }
        )
    except (ValidationError, AttributeError, IndexError, TypeError):
        # Do not chain: validation errors echo the input, which is student data.
        raise InvalidOutputError("OCR result failed schema validation.", retryable=True) from None


class AzureOCRClient:
    """Same interface as VisionClient: transcribe_page / finalize_pages / read_cover."""

    handles_rotation = True
    roll_number_source = "ocr"

    def __init__(self, client: AnalyzeAPI, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.azure_max_concurrency)
        self._resume_at = 0.0
        self._connection_failures = 0
        self.daily_limit_reached = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "AzureOCRClient":
        key = settings.azure_di_key.get_secret_value() if settings.azure_di_key else ""
        if not settings.azure_di_endpoint or not key:
            raise VisionAuthError("No Azure Document Intelligence endpoint/key is configured.")
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        client = DocumentIntelligenceClient(
            settings.azure_di_endpoint,
            AzureKeyCredential(key),
            retry_total=0,  # retries are handled by with_retries so the policy is in one place
        )
        return cls(client, settings)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    # --- public interface -------------------------------------------------------------

    async def transcribe_page(self, jpeg: bytes, *, page_number: int) -> VisionResult[OcrPage]:
        return await self._call(jpeg, label=f"ocr page {page_number}")

    def finalize_pages(
        self, pages: Sequence[tuple[int, OcrPage | None]]
    ) -> list[tuple[int, PageTranscription | None]]:
        return segment_pages(pages, low_confidence=self._settings.azure_low_confidence)

    async def read_cover(self, jpeg: bytes) -> VisionResult[CoverPageInfo]:
        result = await self._call(jpeg, label="ocr cover page")
        roll = find_roll_number(result.data, self._settings.roll_number_pattern)
        return VisionResult(data=CoverPageInfo(roll_number=roll), usage=result.usage)

    # --- internals --------------------------------------------------------------------

    async def _pause_if_needed(self) -> None:
        delay = self._resume_at - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _pause_all(self, seconds: float) -> None:
        now = asyncio.get_running_loop().time()
        self._resume_at = max(self._resume_at, now + seconds)

    async def _analyze(self, jpeg: bytes) -> Any:
        poller = await self._client.begin_analyze_document(
            self._settings.azure_di_model, io.BytesIO(jpeg)
        )
        return await poller.result()

    def _translate(self, exc: Exception) -> Exception:
        """Map SDK errors to ours; never carry the response body (keep messages generic)."""
        s = self._settings
        if isinstance(exc, ClientAuthenticationError):
            return VisionAuthError("Azure rejected the Document Intelligence key.")
        if isinstance(exc, HttpResponseError):
            status = exc.status_code or 0
            message = str(exc).lower()
            if status == 403 and "quota" in message:
                self.daily_limit_reached = True
                logger.error("Azure quota reached; remaining calls skipped")
                return DailyLimitError("Azure Document Intelligence quota reached.")
            if status in (401, 403):
                return VisionAuthError("Azure rejected the Document Intelligence key.")
            if status == 429:
                wait = _retry_after(exc)
                self._pause_all(max(wait or 0.0, RATE_LIMIT_MIN_PAUSE_S))
                return AzureRateLimitedError("Azure rate limit (429).", wait)
            if status in RETRYABLE_STATUS or status >= 500:
                return AzureTransientError(f"Azure service error ({status}).")
            return VisionError(f"Azure rejected the page ({status}).")
        if isinstance(exc, (ServiceRequestError, ServiceResponseError)):
            self._pause_all(backoff_delay(self._connection_failures,
                                          base=max(s.vision_backoff_base_s, 1.0),
                                          cap=s.vision_backoff_max_s, rand=lambda: 1.0))
            self._connection_failures += 1
            return AzureTransientError(f"Azure connection error ({type(exc).__name__}).")
        return exc

    async def _call(self, jpeg: bytes, *, label: str) -> VisionResult[OcrPage]:
        s = self._settings
        usage = Usage()
        invalid_outputs = 0

        async def attempt() -> OcrPage:
            nonlocal invalid_outputs
            async with self._semaphore:
                if self.daily_limit_reached:
                    raise DailyLimitError("Azure quota reached; call not sent.")
                await self._pause_if_needed()
                try:
                    result = await asyncio.wait_for(self._analyze(jpeg),
                                                    timeout=s.vision_call_timeout_s)
                except (HttpResponseError, ServiceRequestError, ServiceResponseError) as exc:
                    raise self._translate(exc) from None
            self._connection_failures = 0
            usage.add(Usage(api_calls=1))
            try:
                return to_ocr_page(result)
            except InvalidOutputError as exc:
                invalid_outputs += 1
                exc.retryable = invalid_outputs <= 1
                raise

        data = await with_retries(
            attempt,
            retries=s.vision_max_retries,
            base=s.vision_backoff_base_s,
            cap=s.vision_backoff_max_s,
            label=label,
            rate_limit_retries=s.vision_rate_limit_retries,
        )
        logger.info("%s ok: %d lines, angle %.0f", label, len(data.lines), data.angle)
        return VisionResult(data=data, usage=usage)
