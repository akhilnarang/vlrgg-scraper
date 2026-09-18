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


class _FakeLivePipeline:
    """Records live-coordination pipeline commands and applies them on execute."""

    def __init__(self, redis, fail=False):
        self.redis = redis
        self.fail = fail
        self.ops = []

    def sadd(self, key, *members):
        self.ops.append(lambda: self.redis.sets.setdefault(key, set()).update(members))
        return self

    def srem(self, key, *members):
        self.ops.append(lambda: self.redis.sets.get(key, set()).difference_update(members))
        return self

    def zadd(self, key, mapping):
        self.ops.append(lambda: self.redis.zsets.setdefault(key, {}).update(mapping))
        return self

    def zrem(self, key, member):
        self.ops.append(lambda: self.redis.zsets.get(key, {}).pop(member, None))
        return self

    def zremrangebyscore(self, key, minimum, maximum):
        def prune():
            zset = self.redis.zsets.get(key, {})
            for member in [name for name, score in zset.items() if score <= maximum]:
                del zset[member]

        self.ops.append(prune)
        return self

    def zcard(self, key):
        self.ops.append(lambda: len(self.redis.zsets.get(key, {})))
        return self

    def expire(self, key, ttl):
        self.ops.append(lambda: True)
        return self

    def delete(self, *keys):
        def remove():
            for key in keys:
                self.redis.zsets.pop(key, None)
                self.redis.sets.pop(key, None)
                self.redis.values.pop(key, None)

        self.ops.append(remove)
        return self

    async def execute(self):
        if self.fail:
            from redis.exceptions import RedisError

            raise RedisError("redis unavailable")
        return [op() for op in self.ops]


class FakeLiveRedis:
    """Minimal async Redis stand-in for the live-match coordination contracts.

    ``mget_script`` lets a test drive ``mget`` results in order; an exception entry is
    raised when reached, which simulates a Redis outage mid-stream. ``fail_pipeline``
    makes every pipeline execution raise, simulating a Redis outage at admission.
    """

    def __init__(self, mget_script=None, fail_pipeline=False):
        self.sets: dict[str, set] = {}
        self.zsets: dict[str, dict] = {}
        self.values: dict[str, bytes] = {}
        self.mget_script = list(mget_script) if mget_script is not None else None
        self.fail_pipeline = fail_pipeline

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def mget(self, keys):
        if self.mget_script is None:
            return [self.values.get(key) for key in keys]
        if not self.mget_script:
            from redis.exceptions import RedisError

            raise RedisError("redis unavailable")
        result = self.mget_script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def set(self, key, value, ex=None):
        self.values[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    async def incr(self, key):
        value = int(self.values.get(key, b"0")) + 1
        self.values[key] = str(value).encode("utf-8")
        return value

    def pipeline(self, transaction=False):
        return _FakeLivePipeline(self, fail=self.fail_pipeline)


@pytest.fixture
def live_redis():
    """Factory for ``FakeLiveRedis`` used by live-match API and cron tests."""
    return FakeLiveRedis


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
