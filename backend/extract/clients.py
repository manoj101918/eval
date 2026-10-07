"""Transcription client interface and the provider switch (TRANSCRIBE_PROVIDER)."""

from collections.abc import Sequence
from typing import Any, Protocol

from backend.config import Settings
from backend.extract.schemas import CoverPageInfo, PageTranscription
from backend.extract.vision import VisionResult


class TranscriptionClient(Protocol):
    handles_rotation: bool  # True: the service reads rotated pages itself
    daily_limit_reached: bool

    async def transcribe_page(self, jpeg: bytes, *, page_number: int) -> VisionResult[Any]: ...

    def finalize_pages(
        self, pages: Sequence[tuple[int, Any]]
    ) -> list[tuple[int, PageTranscription | None]]: ...

    async def read_cover(self, jpeg: bytes) -> VisionResult[CoverPageInfo]: ...

    async def aclose(self) -> None: ...


def make_client(settings: Settings) -> TranscriptionClient:
    if settings.transcribe_provider == "azure":
        from backend.extract.azure_ocr import AzureOCRClient

        return AzureOCRClient.from_settings(settings)
    from backend.extract.vision import VisionClient

    return VisionClient.from_settings(settings)
