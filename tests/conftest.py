from concurrent.futures import Executor, Future
from unittest.mock import AsyncMock

import pytest

from app.exceptions import ScrapingError
from tests.live_upstream import UPSTREAM_NETWORK_ERRORS, is_upstream_outage

LIVE_MARKERS = ("live_golden", "live_health")


@pytest.fixture
def http_response():
    """Build the small HTTP response surface used by service tests."""

    def build(url: str, content: bytes, status: int = 200):
        response = AsyncMock()
        response.status_code = status
        response.content = content
        response.url = url
        return response

    return build


@pytest.fixture
def http_get(http_response):
    """Route mocked HTTP GET calls by URL."""

    def build(pages: dict[str, bytes], fallback: bytes = b"<html><body></body></html>", failures=None):
        failures = failures or {}

        async def get(url: str, *_args, **_kwargs):
            return http_response(url, pages.get(url, fallback), failures.get(url, 200))

        return get

    return build


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Turn VLR outages into skips for every live test, in any directory.

    Applied by marker rather than by fixture so that a new live test cannot
    forget to opt in -- the same drift that previously left the daily job
    silently skipping everything.
    """
    outcome = yield
    if not any(marker in item.keywords for marker in LIVE_MARKERS):
        return

    exc = outcome.excinfo[1] if outcome.excinfo else None
    if exc is None:
        return
    if isinstance(exc, ScrapingError) and is_upstream_outage(exc.upstream_status):
        outcome.force_exception(pytest.skip.Exception(f"VLR unreachable: HTTP {exc.upstream_status}"))
    elif isinstance(exc, UPSTREAM_NETWORK_ERRORS):
        outcome.force_exception(pytest.skip.Exception(f"VLR network error: {exc!r}"))


class InlineExecutor(Executor):
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - executors preserve BaseException semantics
            future.set_exception(exc)
        return future


@pytest.fixture(autouse=True)
def use_inline_executor_for_rankings(monkeypatch):
    from app.services import rankings

    monkeypatch.setattr(rankings, "ProcessPoolExecutor", InlineExecutor)
