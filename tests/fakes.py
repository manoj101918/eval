"""Test double for the Groq async client. Returns real SDK `ChatCompletion` objects."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

import groq
import httpx
from groq.types.chat import ChatCompletion

Handler = Callable[[dict[str, Any]], ChatCompletion | BaseException]


def message(
    payload: dict | str | None,
    *,
    finish_reason: str = "stop",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cached: int = 0,
) -> ChatCompletion:
    text = payload if payload is None or isinstance(payload, str) else json.dumps(payload)
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen/qwen3.8-27b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }
    )


def api_error(cls: type[groq.APIStatusError], status: int, headers: dict | None = None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls("error", response=response, body=None)


def connection_error() -> groq.APIConnectionError:
    return groq.APIConnectionError(
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    )


def user_text(request: dict[str, Any]) -> str:
    return request["messages"][-1]["content"][-1]["text"]


def page_number_of(request: dict[str, Any]) -> int | None:
    """Page number from a transcription request's instruction text (None for the cover call)."""
    m = re.search(r"page (\d+) of the script", user_text(request))
    return int(m.group(1)) if m else None


def scripted(*outcomes: ChatCompletion | BaseException) -> Handler:
    """Handler returning `outcomes` in order, one per call."""
    queue = list(outcomes)

    def handler(_request: dict[str, Any]) -> ChatCompletion | BaseException:
        return queue.pop(0)

    return handler


class FakeCompletions:
    def __init__(self, handler: Handler, delay: Callable[[dict[str, Any]], float]) -> None:
        self._handler = handler
        self._delay = delay
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def create(self, **request: Any) -> ChatCompletion:
        self.calls.append(request)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay(request))
            outcome = self._handler(request)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        finally:
            self.in_flight -= 1


class _Chat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeGroq:
    def __init__(self, handler: Handler, delay: float | Callable[[dict], float] = 0.0) -> None:
        delay_fn = delay if callable(delay) else (lambda _r: delay)
        self.completions = FakeCompletions(handler, delay_fn)
        self.chat = _Chat(self.completions)
