"""Groq vision-model transcription client. Request handling (concurrency, pacing, retries,
validation) lives in backend.llm.groq_chat; this module builds the vision prompts."""

import base64
from collections.abc import Sequence

from backend.config import Settings
from backend.extract import prompts
from backend.extract.schemas import CoverPageInfo, OrientationCheck, PageTranscription
from backend.llm.groq_chat import (  # re-exported: other modules import errors from here
    DAILY_LIMITS,
    PAGE_FAILURES,
    DailyLimitError,
    GroqChat,
    GroqChatConfig,
    GroqLike,
    InvalidOutputError,
    VisionAuthError,
    VisionError,
    VisionResult,
    make_async_groq,
    rate_limit_kind,
    strict_json_schema,
)

__all__ = [
    "DAILY_LIMITS", "PAGE_FAILURES", "DailyLimitError", "InvalidOutputError", "VisionAuthError",
    "VisionClient", "VisionError", "VisionResult", "rate_limit_kind", "strict_json_schema",
]

COVER_MAX_TOKENS = 256
ORIENTATION_MAX_TOKENS = 64


def vision_chat_config(s: Settings) -> GroqChatConfig:
    return GroqChatConfig(
        model=s.vision_model,
        reasoning_effort=s.vision_reasoning_effort,
        response_format=s.vision_response_format,
        max_concurrency=s.vision_max_concurrency,
        tokens_per_minute=s.vision_tokens_per_minute,
        call_timeout_s=s.vision_call_timeout_s,
        max_retries=s.vision_max_retries,
        rate_limit_retries=s.vision_rate_limit_retries,
        backoff_base_s=s.vision_backoff_base_s,
        backoff_max_s=s.vision_backoff_max_s,
    )


class VisionClient:
    handles_rotation = False  # pages are rotated locally (orientation check) before sending
    roll_number_source = "vision"

    def __init__(self, client: GroqLike, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._chat = GroqChat(client, vision_chat_config(settings))

    @classmethod
    def from_settings(cls, settings: Settings) -> "VisionClient":
        key = settings.groq_api_key.get_secret_value() if settings.groq_api_key else None
        return cls(make_async_groq(key, settings.vision_call_timeout_s), settings)

    @property
    def daily_limit_reached(self) -> bool:
        return self._chat.daily_limit_reached

    async def aclose(self) -> None:
        await self._chat.aclose()

    def finalize_pages(
        self, pages: Sequence[tuple[int, PageTranscription | None]]
    ) -> list[tuple[int, PageTranscription | None]]:
        return list(pages)  # the model already returns question segments

    async def transcribe_page(
        self, jpeg: bytes, *, page_number: int
    ) -> VisionResult[PageTranscription]:
        return await self._call(
            system=prompts.TRANSCRIBE_SYSTEM,
            instruction=prompts.TRANSCRIBE_USER.format(page_number=page_number),
            jpeg=jpeg, output_model=PageTranscription,
            max_tokens=self._settings.vision_max_tokens, label=f"transcribe page {page_number}",
        )

    async def check_orientation(self, mosaic_jpeg: bytes) -> VisionResult[OrientationCheck]:
        return await self._call(
            system=prompts.ORIENTATION_SYSTEM, instruction=prompts.ORIENTATION_USER,
            jpeg=mosaic_jpeg, output_model=OrientationCheck,
            max_tokens=ORIENTATION_MAX_TOKENS, label="orientation check",
        )

    async def read_cover(self, jpeg: bytes) -> VisionResult[CoverPageInfo]:
        return await self._call(
            system=prompts.COVER_SYSTEM, instruction=prompts.COVER_USER, jpeg=jpeg,
            output_model=CoverPageInfo, max_tokens=COVER_MAX_TOKENS, label="read cover page",
        )

    async def _call(self, *, system, instruction, jpeg, output_model, max_tokens, label):
        data_uri = "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode("ascii")
        return await self._chat.complete(
            system=system,
            user_content=[
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": instruction},
            ],
            output_model=output_model,
            max_tokens=max_tokens,
            label=label,
        )
