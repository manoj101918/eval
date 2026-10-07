"""Reusable Groq JSON chat client: bounded concurrency, per-call timeouts, token pacing,
shared pauses on 429s / network errors, daily-limit detection, and Pydantic validation.

Used by the vision transcription client and by the grader.
"""

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import groq
from pydantic import BaseModel, ValidationError

from backend.extract.ratelimit import TokenRateLimiter
from backend.extract.retry import backoff_delay, retry_after, with_retries
from backend.extract.schemas import Usage

logger = logging.getLogger(__name__)

RATE_LIMIT_MIN_PAUSE_S = 5.0  # Groq's retry-after is sometimes 1s when the window needs longer
DEFAULT_INPUT_ESTIMATE = 3000  # tokens per request, until measured
EXPECTED_OUTPUT_TOKENS = 600
MAX_INVALID_OUTPUT_RETRIES = 1
DAILY_LIMITS = {"RPD", "TPD", "ASD"}

JSON_OBJECT_SUFFIX = (
    "Respond with a single JSON object only, no other text, that matches this JSON schema: "
)


class VisionError(Exception):
    """A model call failed. Messages never contain model output (it may be student data)."""

    retryable = False


class VisionAuthError(VisionError):
    """Credentials are missing or rejected. Fatal for the whole run, not just one item."""


class DailyLimitError(VisionError):
    """The account's daily/monthly quota is used up. Retrying now is pointless, so remaining
    calls fail at once (items are reported as failed and can be rerun later)."""


class InvalidOutputError(VisionError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


# Failures that end one task (a page, a question) after retries; anything else is a bug.
PAGE_FAILURES = (VisionError, groq.APIError, TimeoutError)

_LIMIT_KIND = re.compile(r"\((RPM|RPD|TPM|TPD|ASH|ASD)\)")


def rate_limit_kind(exc: groq.RateLimitError) -> str | None:
    """Which limit a 429 refers to, e.g. 'TPM' or 'TPD', parsed from Groq's message."""
    m = _LIMIT_KIND.search(str(exc))
    return m.group(1) if m else None


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


@dataclass(frozen=True)
class GroqChatConfig:
    model: str
    reasoning_effort: str
    response_format: Literal["json_schema", "json_object"]
    max_concurrency: int
    tokens_per_minute: int  # 0 disables client-side pacing
    call_timeout_s: float
    max_retries: int
    rate_limit_retries: int
    backoff_base_s: float
    backoff_max_s: float
    temperature: float | None = None


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


def usage_of(response: Any) -> Usage:
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


def validate_response[T: BaseModel](response: Any, output_model: type[T]) -> T:
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


def make_async_groq(api_key: str | None, timeout_s: float) -> Any:
    if not api_key:
        raise VisionAuthError("No Groq API key is configured.")
    return groq.AsyncGroq(
        api_key=api_key,
        max_retries=0,  # retries are handled by with_retries so the policy is in one place
        timeout=timeout_s,
    )


class GroqChat:
    def __init__(self, client: GroqLike, config: GroqChatConfig) -> None:
        self._client = client
        self.config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        # After a 429 or a connection failure every call pauses until this loop time, so
        # queued requests do not all fail again at once (and burn their retries).
        self._resume_at = 0.0
        self._connection_failures = 0
        self._limiter = (
            TokenRateLimiter(config.tokens_per_minute) if config.tokens_per_minute else None
        )
        self._input_estimates: dict[str, int] = {}
        self.daily_limit_reached = False

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def _pause_if_needed(self) -> None:
        delay = self._resume_at - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _pause_all(self, seconds: float) -> None:
        now = asyncio.get_running_loop().time()
        self._resume_at = max(self._resume_at, now + seconds)

    def build_request[T: BaseModel](
        self, *, system: str, user_content: str | list[dict[str, Any]],
        output_model: type[T], max_tokens: int,
    ) -> dict[str, Any]:
        cfg = self.config
        schema = strict_json_schema(output_model)
        if cfg.response_format == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": output_model.__name__, "strict": True, "schema": schema},
            }
        else:
            response_format = {"type": "json_object"}
            system = f"{system}\n\n{JSON_OBJECT_SUFFIX}{json.dumps(schema)}"
        request: dict[str, Any] = {
            "model": cfg.model,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": cfg.reasoning_effort,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        if cfg.temperature is not None:
            request["temperature"] = cfg.temperature
        return request

    async def complete[T: BaseModel](
        self,
        *,
        system: str,
        user_content: str | list[dict[str, Any]],
        output_model: type[T],
        max_tokens: int,
        label: str,
    ) -> VisionResult[T]:
        """One JSON completion validated against `output_model`. `label` is logged and must
        never contain student data."""
        cfg = self.config
        usage = Usage()  # failed attempts are billed too, so count every response
        invalid_outputs = 0
        request = self.build_request(system=system, user_content=user_content,
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
                        timeout=cfg.call_timeout_s,
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
                                                  base=max(cfg.backoff_base_s, 1.0),
                                                  cap=cfg.backoff_max_s, rand=lambda: 1.0))
                    self._connection_failures += 1
                    raise
            self._connection_failures = 0
            call_usage = usage_of(response)
            usage.add(call_usage)
            if call_usage.input_tokens:
                self._input_estimates[kind] = call_usage.input_tokens
            if reservation is not None:
                TokenRateLimiter.settle(
                    reservation, call_usage.input_tokens + call_usage.output_tokens
                )
            try:
                return validate_response(response, output_model)
            except InvalidOutputError as exc:
                invalid_outputs += 1
                exc.retryable = invalid_outputs <= MAX_INVALID_OUTPUT_RETRIES
                raise

        data = await with_retries(
            attempt,
            retries=cfg.max_retries,
            base=cfg.backoff_base_s,
            cap=cfg.backoff_max_s,
            label=label,
            rate_limit_retries=cfg.rate_limit_retries,
        )
        logger.info(
            "%s ok: %d calls, %d in / %d out tokens, %d cached",
            label, usage.api_calls, usage.input_tokens, usage.output_tokens,
            usage.cached_input_tokens,
        )
        return VisionResult(data=data, usage=usage)
