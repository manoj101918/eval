"""Async vision-model client (Groq): bounded concurrency, per-call timeouts, retries,
schema validation."""

import asyncio
import base64
import copy
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import groq
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.extract import prompts
from backend.extract.ratelimit import TokenRateLimiter
from backend.extract.retry import backoff_delay, retry_after, with_retries
from backend.extract.schemas import CoverPageInfo, OrientationCheck, PageTranscription, Usage

logger = logging.getLogger(__name__)

COVER_MAX_TOKENS = 256
RATE_LIMIT_MIN_PAUSE_S = 5.0  # Groq's retry-after is sometimes 1s when the window needs longer
DEFAULT_INPUT_ESTIMATE = 3000  # tokens for one page image + prompt, until measured
EXPECTED_OUTPUT_TOKENS = 600
ORIENTATION_MAX_TOKENS = 64
MAX_INVALID_OUTPUT_RETRIES = 1
DAILY_LIMITS = {"RPD", "TPD", "ASD"}


class VisionError(Exception):
    """A vision call failed. Messages never contain model output (it may be student data)."""

    retryable = False


class VisionAuthError(VisionError):
    """Credentials are missing or rejected. Fatal for the whole run, not just one page."""


class DailyLimitError(VisionError):
    """The account's daily/monthly quota is used up. Retrying now is pointless, so remaining
    calls fail at once (pages are reported as failed and can be rerun later)."""


class InvalidOutputError(VisionError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


_LIMIT_KIND = re.compile(r"\((RPM|RPD|TPM|TPD|ASH|ASD)\)")


def rate_limit_kind(exc: groq.RateLimitError) -> str | None:
    """Which limit a 429 refers to, e.g. 'TPM' or 'TPD', parsed from Groq's message."""
    m = _LIMIT_KIND.search(str(exc))
    return m.group(1) if m else None


# Failures that end one vision task (a page) after retries; anything else is a bug.
PAGE_FAILURES = (VisionError, groq.APIError, TimeoutError)


class CompletionsAPI(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class ChatAPI(Protocol):
    completions: CompletionsAPI


class GroqLike(Protocol):
    chat: ChatAPI


@dataclass
class VisionResult[T: BaseModel]:
    data: T
    usage: Usage


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for strict structured outputs: $refs inlined, every object closed
    (additionalProperties false) with all properties required."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return resolve(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
        out = {k: resolve(v) for k, v in node.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return resolve(schema)


def _usage_of(response: Any) -> Usage:
    u = response.usage
    if u is None:
        return Usage(api_calls=1)
    details = getattr(u, "prompt_tokens_details", None)
    return Usage(
        api_calls=1,
        input_tokens=u.prompt_tokens or 0,
        output_tokens=u.completion_tokens or 0,
        cached_input_tokens=(getattr(details, "cached_tokens", None) or 0) if details else 0,
    )


class VisionClient:
    handles_rotation = False  # pages are rotated locally (orientation check) before sending

    def __init__(self, client: GroqLike, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.vision_max_concurrency)
        # After a 429 or a connection failure every call pauses until this loop time, so
        # queued requests do not all fail again at once (and burn their retries).
        self._resume_at = 0.0
        self._connection_failures = 0
        self._limiter = (
            TokenRateLimiter(settings.vision_tokens_per_minute)
            if settings.vision_tokens_per_minute
            else None
        )
        self._input_estimates: dict[str, int] = {}
        self.daily_limit_reached = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "VisionClient":
        if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
            raise VisionAuthError("No Groq API key is configured.")
        client = groq.AsyncGroq(
            api_key=settings.groq_api_key.get_secret_value(),
            max_retries=0,  # retries are handled by with_retries so the policy is in one place
            timeout=settings.vision_call_timeout_s,
        )
        return cls(client, settings)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

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
            jpeg=jpeg,
            output_model=PageTranscription,
            max_tokens=self._settings.vision_max_tokens,
            label=f"transcribe page {page_number}",
        )

    async def check_orientation(self, mosaic_jpeg: bytes) -> VisionResult[OrientationCheck]:
        return await self._call(
            system=prompts.ORIENTATION_SYSTEM,
            instruction=prompts.ORIENTATION_USER,
            jpeg=mosaic_jpeg,
            output_model=OrientationCheck,
            max_tokens=ORIENTATION_MAX_TOKENS,
            label="orientation check",
        )

    async def _pause_if_needed(self) -> None:
        delay = self._resume_at - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _pause_all(self, seconds: float) -> None:
        now = asyncio.get_running_loop().time()
        self._resume_at = max(self._resume_at, now + seconds)

    async def read_cover(self, jpeg: bytes) -> VisionResult[CoverPageInfo]:
        return await self._call(
            system=prompts.COVER_SYSTEM,
            instruction=prompts.COVER_USER,
            jpeg=jpeg,
            output_model=CoverPageInfo,
            max_tokens=COVER_MAX_TOKENS,
            label="read cover page",
        )

    def _build_request[T: BaseModel](
        self, *, system: str, instruction: str, jpeg: bytes, output_model: type[T],
        max_tokens: int,
    ) -> dict[str, Any]:
        s = self._settings
        schema = strict_json_schema(output_model)
        if s.vision_response_format == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": output_model.__name__, "strict": True, "schema": schema},
            }
        else:
            response_format = {"type": "json_object"}
            system = f"{system}\n\n{prompts.JSON_OBJECT_SUFFIX}{json.dumps(schema)}"
        data_uri = "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode("ascii")
        return {
            "model": s.vision_model,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": s.vision_reasoning_effort,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": instruction},
                    ],
                },
            ],
        }

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
        request = self._build_request(system=system, instruction=instruction, jpeg=jpeg,
                                      output_model=output_model, max_tokens=max_tokens)

        kind = output_model.__name__

        async def attempt() -> T:
            nonlocal invalid_outputs
            async with self._semaphore:  # held only while the request is in flight
                if self.daily_limit_reached:
                    raise DailyLimitError("Groq daily limit reached; call not sent.")
                await self._pause_if_needed()
                reservation = None
                if self._limiter is not None:
                    estimate = self._input_estimates.get(kind, DEFAULT_INPUT_ESTIMATE)
                    reservation = await self._limiter.acquire(
                        estimate + min(max_tokens, EXPECTED_OUTPUT_TOKENS)
                    )
                try:
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(**request),
                        timeout=s.vision_call_timeout_s,
                    )
                except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
                    raise VisionAuthError("The Groq API rejected the API key.") from exc
                except groq.RateLimitError as exc:
                    limit = rate_limit_kind(exc)
                    if limit in DAILY_LIMITS:
                        self.daily_limit_reached = True
                        logger.error("Groq daily limit (%s) reached; remaining calls skipped",
                                     limit)
                        raise DailyLimitError(f"Groq daily limit ({limit}) reached.") from None
                    logger.info("rate limited (%s)", limit or "unknown limit")
                    self._pause_all(max(retry_after(exc) or 0.0, RATE_LIMIT_MIN_PAUSE_S))
                    raise
                except groq.APIConnectionError:
                    # Network blips hit every queued call alike: pause them all, growing.
                    self._pause_all(backoff_delay(self._connection_failures,
                                                  base=max(s.vision_backoff_base_s, 1.0),
                                                  cap=s.vision_backoff_max_s,
                                                  rand=lambda: 1.0))
                    self._connection_failures += 1
                    raise
            self._connection_failures = 0
            call_usage = _usage_of(response)
            usage.add(call_usage)
            if call_usage.input_tokens:
                self._input_estimates[kind] = call_usage.input_tokens
            if reservation is not None:
                TokenRateLimiter.settle(
                    reservation, call_usage.input_tokens + call_usage.output_tokens
                )
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
            rate_limit_retries=s.vision_rate_limit_retries,
        )
        logger.info(
            "%s ok: %d calls, %d in / %d out tokens, %d cached",
            label, usage.api_calls, usage.input_tokens, usage.output_tokens,
            usage.cached_input_tokens,
        )
        return VisionResult(data=data, usage=usage)


def _validate[T: BaseModel](response: Any, output_model: type[T]) -> T:
    if not response.choices:
        raise InvalidOutputError("Response had no choices.", retryable=True)
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise InvalidOutputError("Output was truncated at max_completion_tokens.",
                                 retryable=True)
    text = choice.message.content
    if not text:
        raise InvalidOutputError("Response had no content.", retryable=True)
    try:
        return output_model.model_validate_json(text)
    except ValidationError as exc:
        # Do not chain: pydantic errors echo the input, which is student data.
        raise InvalidOutputError(
            f"Output failed schema validation ({exc.error_count()} errors).", retryable=True
        ) from None
