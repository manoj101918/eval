"""Async vision-model client: bounded concurrency, per-call timeouts, retries, schema validation."""

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.extract import prompts
from backend.extract.retry import with_retries
from backend.extract.schemas import CoverPageInfo, PageTranscription, Usage

logger = logging.getLogger(__name__)

COVER_MAX_TOKENS = 256
MAX_INVALID_OUTPUT_RETRIES = 1


class VisionError(Exception):
    """A vision call failed. Messages never contain model output (it may be student data)."""

    retryable = False


class OutputRefusedError(VisionError):
    pass


class InvalidOutputError(VisionError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class MessagesAPI(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class AnthropicLike(Protocol):
    messages: MessagesAPI


@dataclass
class VisionResult[T: BaseModel]:
    data: T
    usage: Usage


def _usage_of(response: Any) -> Usage:
    u = response.usage
    return Usage(
        api_calls=1,
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
    )


class VisionClient:
    def __init__(self, client: AnthropicLike, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.vision_max_concurrency)

    @classmethod
    def from_settings(cls, settings: Settings) -> "VisionClient":
        key = settings.anthropic_api_key
        api_key = key.get_secret_value() if key else None  # None: SDK resolves env / profile
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=0,  # retries are handled by with_retries so the policy is in one place
            timeout=settings.vision_call_timeout_s,
        )
        return cls(client, settings)

    async def transcribe_page(
        self, jpeg: bytes, *, page_number: int
    ) -> VisionResult[PageTranscription]:
        return await self._call(
            system=prompts.TRANSCRIBE_SYSTEM,
            instruction=prompts.TRANSCRIBE_USER.format(page_number=page_number),
            jpeg=jpeg,
            output_model=PageTranscription,
            max_tokens=self._settings.vision_max_tokens,
            label=f"transcribe page {page_number}",
        )

    async def read_cover(self, jpeg: bytes) -> VisionResult[CoverPageInfo]:
        return await self._call(
            system=prompts.COVER_SYSTEM,
            instruction=prompts.COVER_USER,
            jpeg=jpeg,
            output_model=CoverPageInfo,
            max_tokens=COVER_MAX_TOKENS,
            label="read cover page",
        )

    async def _call[T: BaseModel](
        self,
        *,
        system: str,
        instruction: str,
        jpeg: bytes,
        output_model: type[T],
        max_tokens: int,
        label: str,
    ) -> VisionResult[T]:
        s = self._settings
        usage = Usage()  # failed attempts are billed too, so count every response
        invalid_outputs = 0
        schema = anthropic.transform_schema(output_model)
        request = {
            "model": s.transcribe_model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.standard_b64encode(jpeg).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }

        async def attempt() -> T:
            nonlocal invalid_outputs
            async with self._semaphore:  # held only while the request is in flight
                response = await asyncio.wait_for(
                    self._client.messages.create(**request), timeout=s.vision_call_timeout_s
                )
            usage.add(_usage_of(response))
            try:
                return _validate(response, output_model)
            except InvalidOutputError as exc:
                invalid_outputs += 1
                exc.retryable = invalid_outputs <= MAX_INVALID_OUTPUT_RETRIES
                raise

        data = await with_retries(
            attempt,
            retries=s.vision_max_retries,
            base=s.vision_backoff_base_s,
            cap=s.vision_backoff_max_s,
            label=label,
        )
        logger.info(
            "%s ok: %d calls, %d in / %d out tokens, %d cache read",
            label, usage.api_calls, usage.input_tokens, usage.output_tokens,
            usage.cache_read_input_tokens,
        )
        return VisionResult(data=data, usage=usage)


def _validate[T: BaseModel](response: Any, output_model: type[T]) -> T:
    if response.stop_reason == "refusal":
        raise OutputRefusedError("The model declined to process this page.")
    if response.stop_reason == "max_tokens":
        raise InvalidOutputError("Output was truncated at max_tokens.", retryable=True)
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise InvalidOutputError("Response had no text block.", retryable=True)
    try:
        return output_model.model_validate_json(text)
    except ValidationError as exc:
        # Do not chain: pydantic errors echo the input, which is student data.
        raise InvalidOutputError(
            f"Output failed schema validation ({exc.error_count()} errors).", retryable=True
        ) from None
