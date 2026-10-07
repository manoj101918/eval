import groq
import pytest

from backend.extract.retry import backoff_delay, is_retryable, retry_after, with_retries
from tests.fakes import api_error, connection_error


class Recorder:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def flaky(failures):
    """Async fn that raises each of `failures` in turn, then returns 'ok'."""
    remaining = list(failures)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if remaining:
            raise remaining.pop(0)
        return "ok"

    return fn, calls


def test_backoff_grows_exponentially_and_caps():
    delays = [backoff_delay(n, base=1.0, cap=5.0, rand=lambda: 1.0) for n in range(5)]
    assert delays == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_backoff_has_jitter():
    assert backoff_delay(3, base=1.0, cap=30, rand=lambda: 0.25) == 2.0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError(), True),
        (connection_error(), True),
        (api_error(groq.RateLimitError, 429), True),
        (api_error(groq.InternalServerError, 500), True),
        (api_error(groq.APIStatusError, 503), True),  # over capacity
        (api_error(groq.BadRequestError, 400), False),
        (api_error(groq.AuthenticationError, 401), False),
        (api_error(groq.PermissionDeniedError, 403), False),
        (ValueError("bug"), False),
    ],
)
def test_is_retryable(exc, expected):
    assert is_retryable(exc) is expected


def test_retry_after_header():
    assert retry_after(api_error(groq.RateLimitError, 429, {"retry-after": "7"})) == 7.0
    assert retry_after(api_error(groq.RateLimitError, 429, {"retry-after": "999"})) == 60.0
    assert retry_after(api_error(groq.RateLimitError, 429, {"retry-after": "soon"})) is None
    assert retry_after(api_error(groq.RateLimitError, 429)) is None
    assert retry_after(TimeoutError()) is None


async def test_succeeds_after_transient_errors():
    fn, calls = flaky([connection_error(), api_error(groq.InternalServerError, 503)])
    sleep = Recorder()
    assert await with_retries(fn, retries=4, base=1.0, cap=30, sleep=sleep) == "ok"
    assert calls["n"] == 3
    assert len(sleep.delays) == 2
    assert sleep.delays[0] <= 1.0 and sleep.delays[1] <= 2.0


async def test_timeout_is_retried():
    fn, calls = flaky([TimeoutError()])
    assert await with_retries(fn, retries=2, base=0, cap=0, sleep=Recorder()) == "ok"
    assert calls["n"] == 2


async def test_honours_retry_after():
    fn, _ = flaky([api_error(groq.RateLimitError, 429, {"retry-after": "3"})])
    sleep = Recorder()
    await with_retries(fn, retries=2, base=1.0, cap=30, sleep=sleep)
    assert sleep.delays == [3.0]


async def test_bad_request_not_retried():
    fn, calls = flaky([api_error(groq.BadRequestError, 400)])
    with pytest.raises(groq.BadRequestError):
        await with_retries(fn, retries=4, base=0, cap=0, sleep=Recorder())
    assert calls["n"] == 1


async def test_gives_up_after_max_retries():
    fn, calls = flaky([TimeoutError()] * 10)
    sleep = Recorder()
    with pytest.raises(TimeoutError):
        await with_retries(fn, retries=3, base=0, cap=0, sleep=sleep)
    assert calls["n"] == 4  # 1 attempt + 3 retries
    assert len(sleep.delays) == 3


async def test_zero_retries():
    fn, calls = flaky([TimeoutError()])
    with pytest.raises(TimeoutError):
        await with_retries(fn, retries=0, base=0, cap=0, sleep=Recorder())
    assert calls["n"] == 1
