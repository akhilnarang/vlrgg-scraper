"""Shared handling for VLR being unreachable during live tests.

A parser break is actionable: we fix a selector. VLR returning 503 at 03:00 UTC
is not: we wait. Conflating them trains the reader to ignore the daily job, which
is how a real break goes unnoticed -- so upstream outages skip rather than fail.

The complement lives in `tests/live/check_executed.py`: if *everything* skips, the
job must go red rather than report a green no-op.
"""

import httpx
import pytest

from app.exceptions import ScrapingError


UPSTREAM_NETWORK_ERRORS = (httpx.TransportError, httpx.TimeoutException)

# 5xx and 429 are VLR's problem and clear on their own. Anything else -- a 404 on a
# pinned historical ID, say -- means a URL scheme or config change we must act on,
# so it is deliberately left to fail.
UPSTREAM_STATUSES = frozenset({429, 500, 502, 503, 504})


def skip_if_upstream_down(exc: BaseException) -> None:
    """Skip the current test if `exc` is VLR being unavailable, else re-raise."""
    if isinstance(exc, ScrapingError):
        if exc.upstream_status in UPSTREAM_STATUSES:
            pytest.skip(f"VLR unreachable: HTTP {exc.upstream_status}")
        raise exc
    if isinstance(exc, UPSTREAM_NETWORK_ERRORS):
        pytest.skip(f"VLR network error: {exc!r}")
    raise exc
