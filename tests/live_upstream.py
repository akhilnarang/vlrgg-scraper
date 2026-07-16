"""Shared handling for VLR being unreachable during live tests.

A parser break is actionable: we fix a selector. VLR returning 503 at 03:00 UTC
is not: we wait. Conflating them trains the reader to ignore the daily job, which
is how a real break goes unnoticed -- so upstream outages skip rather than fail.

The complement lives in `tests/live/check_executed.py`: if *everything* skips, the
job must go red rather than report a green no-op.
"""

import httpx


UPSTREAM_NETWORK_ERRORS = (httpx.TransportError, httpx.TimeoutException)

# 429 plus any 5xx: VLR's problem, and it clears on its own.
UPSTREAM_STATUSES = frozenset({429})


def is_upstream_outage(status: int) -> bool:
    """Whether `status` means VLR is unavailable rather than the parser being wrong.

    Anything else -- a 404 on a pinned historical ID, say -- means a URL scheme or
    config change we must act on, so it is deliberately left to fail.
    """
    return status in UPSTREAM_STATUSES or 500 <= status <= 599
