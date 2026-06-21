import pytest

from app.agent.ratelimit import client_ip, enforce_rate_limit
from app.exceptions import RateLimitError


class FakePipe:
    def __init__(self, count):
        self._count, self.ops = count, []

    def zremrangebyscore(self, *a):
        self.ops.append("zrem")
        return self

    def zcard(self, *a):
        self.ops.append("zcard")
        return self

    def zadd(self, *a):
        self.ops.append("zadd")
        return self

    def expire(self, *a):
        self.ops.append("expire")
        return self

    async def execute(self):
        return [0, self._count, 1, True]


class FakeRedis:
    def __init__(self, count):
        self._count = count

    def pipeline(self, *a, **k):
        return FakePipe(self._count)


def test_client_ip_prefers_leftmost_xff():
    assert client_ip("9.9.9.9, 10.0.0.1", "1.2.3.4") == "9.9.9.9"


def test_client_ip_falls_back_to_peer():
    assert client_ip(None, "1.2.3.4") == "1.2.3.4"


def test_client_ip_unknown_when_nothing():
    assert client_ip(None, None) == "unknown"
    assert client_ip("   ", "5.6.7.8") == "5.6.7.8"  # blank xff ignored


@pytest.mark.asyncio
async def test_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_ENABLED", False)
    await enforce_rate_limit(FakeRedis(count=999), "1.2.3.4")  # must not raise


@pytest.mark.asyncio
async def test_blocks_over_limit(monkeypatch):
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT", 5)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_WINDOW", 60)
    with pytest.raises(RateLimitError) as ei:
        await enforce_rate_limit(FakeRedis(count=5), "1.2.3.4")
    assert ei.value.status_code == 429
    assert ei.value.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_allows_under_limit(monkeypatch):
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT", 5)
    await enforce_rate_limit(FakeRedis(count=2), "1.2.3.4")  # must not raise


@pytest.mark.asyncio
async def test_two_requests_same_timestamp_both_counted(monkeypatch):
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT", 100)
    monkeypatch.setattr("app.agent.ratelimit.time.time", lambda: 1000.0)  # frozen clock

    store = {}

    class StorePipe:
        def __init__(self, s):
            self.s = s

        def zremrangebyscore(self, *a):
            return self

        def zcard(self, *a):
            return self

        def zadd(self, key, mapping):
            self.s.update(mapping)
            return self

        def expire(self, *a):
            return self

        async def execute(self):
            return [0, len(self.s), 1, True]

    class StoreRedis:
        def __init__(self, s):
            self.s = s

        def pipeline(self, *a, **k):
            return StorePipe(self.s)

    r = StoreRedis(store)
    await enforce_rate_limit(r, "1.2.3.4")
    await enforce_rate_limit(r, "1.2.3.4")
    assert len(store) == 2  # two distinct members despite identical timestamp
