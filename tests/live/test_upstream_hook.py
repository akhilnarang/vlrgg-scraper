import pytest

from app.exceptions import ScrapingError
from tests.live_upstream import is_upstream_outage


def test_upstream_outages_are_separate_from_actionable_responses():
    assert is_upstream_outage(429)
    assert is_upstream_outage(503)
    assert not is_upstream_outage(404)


@pytest.mark.live_health
def test_live_hook_skips_an_upstream_outage():
    raise ScrapingError(url="https://vlr.gg/example", upstream_status=503)
