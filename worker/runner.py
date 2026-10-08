"""Background job runners.

InlineRunner processes jobs one at a time inside the API process (development and tests,
no Redis needed). One job at a time keeps the Azure/Groq pacing limits accurate. The arq
runner (Redis) for deployment arrives in phase 5 with the same interface.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger(__name__)

Handler = Callable[[int], Awaitable[None]]


class JobRunner(Protocol):
    def enqueue(self, script_id: int) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class InlineRunner:
    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def enqueue(self, script_id: int) -> None:
        self._queue.put_nowait(script_id)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        close = getattr(self._handler, "aclose", None)  # e.g. the AI clients' sessions
        if close is not None:
            await close()

    async def join(self) -> None:
        """Wait until every queued job has finished (tests)."""
        await self._queue.join()

    async def _loop(self) -> None:
        while True:
            script_id = await self._queue.get()
            try:
                await self._handler(script_id)
            except Exception as exc:  # a crashed job must not stop the runner
                # Only the type: messages can carry paths or data.
                logger.error("job for script %d crashed: %s", script_id, type(exc).__name__)
            finally:
                self._queue.task_done()
