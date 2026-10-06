"""Test double for the Anthropic async client. Returns real SDK `Message` objects."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
from anthropic.types import Message

Handler = Callable[[dict[str, Any]], Message | BaseException]


def message(
    payload: dict | str,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read: int = 0,
) -> Message:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
        }
    )


def api_error(cls: type[anthropic.APIStatusError], status: int, headers: dict | None = None):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, headers=headers or {})
    return cls("error", response=response, body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def page_number_of(request: dict[str, Any]) -> int | None:
    """Page number from a transcription request's instruction text (None for the cover call)."""
    text = request["messages"][0]["content"][-1]["text"]
    m = re.search(r"page (\d+) of the script", text)
    return int(m.group(1)) if m else None


def scripted(*outcomes: Message | BaseException) -> Handler:
    """Handler returning `outcomes` in order, one per call."""
    queue = list(outcomes)

    def handler(_request: dict[str, Any]) -> Message | BaseException:
        return queue.pop(0)

    return handler


class FakeMessages:
    def __init__(self, handler: Handler, delay: Callable[[dict[str, Any]], float]) -> None:
        self._handler = handler
        self._delay = delay
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def create(self, **request: Any) -> Message:
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


class FakeAnthropic:
    def __init__(self, handler: Handler, delay: float | Callable[[dict], float] = 0.0) -> None:
        delay_fn = delay if callable(delay) else (lambda _r: delay)
        self.messages = FakeMessages(handler, delay_fn)
