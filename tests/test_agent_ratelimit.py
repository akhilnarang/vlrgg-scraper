import pytest

from app.agent.ratelimit import client_ip, enforce_rate_limit
from app.exceptions import RateLimitError


class FakePipe:
    def __init__(self, count):
        self.count = count

    def zremrangebyscore(self, *_args):
        return self

    def zcard(self, *_args):
        return self

    def zadd(self, *_args):
        return self

    def expire(self, *_args):
        return self

    async def execute(self):
        return [0, self.count, 1, True]


class FakeRedis:
    def __init__(self, count):
        self.count = count

    def pipeline(self, *_args, **_kwargs):
        return FakePipe(self.count)


def test_client_ip_prefers_forwarded_for_then_peer():
    assert client_ip("9.9.9.9, 10.0.0.1", "1.2.3.4") == "9.9.9.9"
    assert client_ip(None, "1.2.3.4") == "1.2.3.4"


@pytest.mark.asyncio
async def test_rate_limit_allows_then_blocks(monkeypatch):
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT", 5)
    monkeypatch.setattr("app.agent.ratelimit.settings.LLM_RATE_LIMIT_WINDOW", 60)

    await enforce_rate_limit(FakeRedis(count=2), "1.2.3.4")

    with pytest.raises(RateLimitError) as error:
        await enforce_rate_limit(FakeRedis(count=5), "1.2.3.4")

    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "60"}
