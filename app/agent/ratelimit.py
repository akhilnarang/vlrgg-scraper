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


async def enforce_rate_limit(
    redis_client,
    identifier: str,
    *,
    enabled: bool | None = None,
    limit: int | None = None,
    window: int | None = None,
    prefix: str = "ask",
) -> None:
    """Per-identifier Redis sliding-window limit. No-op unless enabled.

    ``enabled``/``limit``/``window`` default to the ``LLM_RATE_LIMIT_*`` settings when
    left as ``None``, and ``prefix`` namespaces the Redis key so callers don't share buckets.
    """
    if not (settings.LLM_RATE_LIMIT_ENABLED if enabled is None else enabled):
        return
    now = time.time()
    window = settings.LLM_RATE_LIMIT_WINDOW if window is None else window
    limit = settings.LLM_RATE_LIMIT if limit is None else limit
    key = f"{prefix}:ratelimit:{identifier}"
    pipe = redis_client.pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, now - window)
    pipe.zcard(key)
    pipe.zadd(key, {f"{now}:{uuid.uuid4()}": now})
    pipe.expire(key, window)
    _, count, _, _ = await pipe.execute()
    if count >= limit:
        raise RateLimitError(retry_after=window)
