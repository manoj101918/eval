"""Async vision-model client (Groq): bounded concurrency, per-call timeouts, retries,
schema validation."""

import asyncio
import base64
import copy
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import groq
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


class VisionAuthError(VisionError):
    """Credentials are missing or rejected. Fatal for the whole run, not just one page."""


class InvalidOutputError(VisionError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


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
    def __init__(self, client: GroqLike, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.vision_max_concurrency)

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

        async def attempt() -> T:
            nonlocal invalid_outputs
            async with self._semaphore:  # held only while the request is in flight
                try:
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(**request),
                        timeout=s.vision_call_timeout_s,
                    )
                except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
                    raise VisionAuthError("The Groq API rejected the API key.") from exc
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
