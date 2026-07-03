import time
import uuid

from app.core.config import settings
from app.exceptions import RateLimitError


def client_ip(forwarded_for: str | None, fallback: str | None) -> str:
    """Resolve a client IP from an X-Forwarded-For value, falling back to the peer address.

    Trusts the left-most XFF entry — valid only behind a trusted reverse proxy.
    """
    if forwarded_for and (first := forwarded_for.split(",")[0].strip()):
        return first
    return fallback or "unknown"


async def enforce_rate_limit(redis_client, identifier: str) -> None:
    """Per-identifier Redis sliding-window limit. No-op unless enabled in settings."""
    if not settings.LLM_RATE_LIMIT_ENABLED:
        return
    now = time.time()
    window = settings.LLM_RATE_LIMIT_WINDOW
    key = f"ask:ratelimit:{identifier}"
    pipe = redis_client.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, now - window)
    pipe.zcard(key)
    pipe.zadd(key, {f"{now}:{uuid.uuid4()}": now})
    pipe.expire(key, window)
    _, count, _, _ = await pipe.execute()
    if count >= settings.LLM_RATE_LIMIT:
        raise RateLimitError(retry_after=window)
