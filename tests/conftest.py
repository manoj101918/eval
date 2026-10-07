import pytest


@pytest.fixture(autouse=True)
def _no_client_side_pacing(monkeypatch):
    """Fake API calls must not wait on the real tokens-per-minute pacing.

    Tests that exercise the limiter pass `vision_tokens_per_minute` explicitly.
    """
    monkeypatch.setenv("VISION_TOKENS_PER_MINUTE", "0")
    monkeypatch.setenv("GRADING_TOKENS_PER_MINUTE", "0")
