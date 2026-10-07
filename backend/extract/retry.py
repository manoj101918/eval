"""Async retry with exponential backoff and full jitter."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import groq

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 429}
MAX_RETRY_AFTER_S = 60.0


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):  # asyncio.wait_for timeout
        return True
    if isinstance(exc, groq.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, groq.APIStatusError):
        return exc.status_code in RETRYABLE_STATUS or exc.status_code >= 500
    return bool(getattr(exc, "retryable", False))


def backoff_delay(
    attempt: int, *, base: float, cap: float, rand: Callable[[], float] = random.random
) -> float:
    """Full-jitter backoff: uniform in [0, min(cap, base * 2**attempt)]."""
    return rand() * min(cap, base * (2**attempt))


def retry_after(exc: BaseException) -> float | None:
    """Server-requested delay (seconds) from a `retry-after` header, if any."""
    if not isinstance(exc, groq.APIStatusError):
        return None
    value = exc.response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER_S) if value is not None else None
    except ValueError:
        return None


async def with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int,
    base: float,
    cap: float,
    label: str = "call",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `fn`, retrying retryable failures up to `retries` times.

    `label` is logged; it must never contain student data.
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if attempt >= retries or not is_retryable(exc):
                raise
            delay = retry_after(exc)
            if delay is None:
                delay = backoff_delay(attempt, base=base, cap=cap)
            attempt += 1
            logger.warning(
                "%s failed with %s; retry %d/%d in %.1fs",
                label, type(exc).__name__, attempt, retries, delay,
            )
            await sleep(delay)
