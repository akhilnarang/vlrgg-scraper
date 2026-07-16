from concurrent.futures import Executor, Future

import pytest

from tests.live_upstream import UPSTREAM_NETWORK_ERRORS, UPSTREAM_STATUSES

from app.exceptions import ScrapingError


LIVE_MARKERS = ("live_golden", "live_health")


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
    if isinstance(exc, ScrapingError) and exc.upstream_status in UPSTREAM_STATUSES:
        outcome.force_exception(pytest.skip.Exception(f"VLR unreachable: HTTP {exc.upstream_status}"))
    elif isinstance(exc, UPSTREAM_NETWORK_ERRORS):
        outcome.force_exception(pytest.skip.Exception(f"VLR network error: {exc!r}"))


class InlineExecutor(Executor):
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.shutdown()

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


@pytest.fixture(autouse=True)
def use_inline_executor_for_rankings(monkeypatch):
    from app.services import rankings

    monkeypatch.setattr(rankings, "ProcessPoolExecutor", InlineExecutor)
