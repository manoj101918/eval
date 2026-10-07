"""Client-side token-per-minute pacing, so requests are spaced out instead of rejected (429)."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

WINDOW_S = 60.0


class Reservation:
    __slots__ = ("at", "tokens")

    def __init__(self, at: float, tokens: int) -> None:
        self.at = at
        self.tokens = tokens


class TokenRateLimiter:
    """Sliding one-minute window of tokens sent.

    `acquire(estimate)` waits until the estimate fits under the limit, then records it;
    `settle()` replaces the estimate with the real usage once the response arrives.
    A single request larger than the whole limit is let through when the window is empty.
    """

    def __init__(
        self,
        tokens_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._limit = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._window: deque[Reservation] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._window and now - self._window[0].at >= WINDOW_S:
            self._window.popleft()

    async def acquire(self, estimate: int) -> Reservation:
        async with self._lock:  # one waiter at a time keeps requests in arrival order
            while True:
                now = self._clock()
                self._prune(now)
                used = sum(r.tokens for r in self._window)
                if not self._window or used + estimate <= self._limit:
                    reservation = Reservation(now, estimate)
                    self._window.append(reservation)
                    return reservation
                await self._sleep(self._window[0].at + WINDOW_S - now)

    @staticmethod
    def settle(reservation: Reservation, actual_tokens: int) -> None:
        reservation.tokens = actual_tokens
