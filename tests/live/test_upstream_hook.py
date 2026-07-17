"""Tests for the upstream-outage hook in the root conftest.

The hook decides whether a live failure is VLR's problem (skip, no action) or
ours (fail, fix a selector). Getting that wrong in either direction defeats the
daily job: skip too much and a real break reports green; skip too little and an
outage reds the job until people stop reading it.

The behaviour was previously only verified by hand, which is how the `5xx` set
drifted from its own docstring -- it listed five codes and missed 501/507/508.
"""

import httpx
import pytest

from app.exceptions import ScrapingError
from tests.live_upstream import is_upstream_outage


@pytest.mark.parametrize("status", [429, 500, 501, 502, 503, 504, 507, 508, 510, 511, 599])
def test_outage_statuses(status):
    """429 and every 5xx clear on their own and must not page anyone."""
    assert is_upstream_outage(status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 418, 499])
def test_actionable_statuses(status):
    """A 404 on a pinned historical ID is a URL/config change we must act on."""
    assert not is_upstream_outage(status)


# The hook is exercised end-to-end below: these tests are marked live_health, so
# the root conftest's pytest_runtest_call wrapper applies to them, but they raise
# locally instead of touching the network.


@pytest.mark.live_health
def test_hook_skips_upstream_5xx():
    """Must be reported as skipped, not failed -- if it runs to the assert, the hook missed it."""
    raise ScrapingError(url="https://vlr.gg/x", upstream_status=503)


@pytest.mark.live_health
def test_hook_skips_network_error():
    raise httpx.ConnectError("connection refused")


@pytest.mark.live_health
def test_hook_skips_timeout():
    raise httpx.ReadTimeout("timed out")


def test_hook_ignores_unmarked_tests():
    """Without a live marker the hook must not intervene, so this failure stands."""
    with pytest.raises(ScrapingError):
        raise ScrapingError(url="https://vlr.gg/x", upstream_status=503)
