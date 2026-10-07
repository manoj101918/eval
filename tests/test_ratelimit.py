import asyncio

from backend.extract.ratelimit import WINDOW_S, TokenRateLimiter


class FakeTime:
    """Deterministic clock; sleeping advances it instantly."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def limiter(tpm, t):
    return TokenRateLimiter(tpm, clock=t.clock, sleep=t.sleep)


async def test_requests_within_limit_do_not_wait():
    t = FakeTime()
    lim = limiter(8000, t)
    await lim.acquire(3000)
    await lim.acquire(3000)
    assert t.sleeps == []


async def test_request_over_limit_waits_for_oldest_to_expire():
    t = FakeTime()
    lim = limiter(8000, t)
    await lim.acquire(3000)
    t.now = 10.0
    await lim.acquire(3000)
    t.now = 20.0
    await lim.acquire(3000)  # 9000 > 8000: wait until the first leaves the window
    assert t.sleeps == [WINDOW_S - 20.0]
    assert t.now == WINDOW_S


async def test_settle_with_actual_usage_frees_capacity():
    t = FakeTime()
    lim = limiter(8000, t)
    r = await lim.acquire(6000)
    TokenRateLimiter.settle(r, 2000)  # the response was smaller than estimated
    await lim.acquire(5000)
    assert t.sleeps == []


async def test_oversized_request_allowed_when_window_empty():
    t = FakeTime()
    lim = limiter(1000, t)
    await lim.acquire(5000)
    assert t.sleeps == []


async def test_concurrent_acquires_are_paced():
    t = FakeTime()
    lim = limiter(6000, t)
    await asyncio.gather(*(lim.acquire(3000) for _ in range(4)))
    # Two fit in the first minute, the next two after the window rolls over.
    assert t.now == WINDOW_S
