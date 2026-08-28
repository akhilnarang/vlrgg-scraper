from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import team

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TEAM_ID = "624"


def _team_pages():
    completed_url = constants.TEAM_COMPLETED_MATCHES_URL.format(TEAM_ID)
    return {
        constants.TEAM_URL.format(TEAM_ID): (FIXTURE_DIR / "team_624.html").read_bytes(),
        constants.TEAM_UPCOMING_MATCHES_URL.format(TEAM_ID): (FIXTURE_DIR / "team_624_upcoming.html").read_bytes(),
        completed_url: (FIXTURE_DIR / "team_624_completed_page1.html").read_bytes(),
        team.completed_matches_url(TEAM_ID, 2): (FIXTURE_DIR / "team_624_completed_page2.html").read_bytes(),
    }


@pytest.mark.asyncio
async def test_team_response_includes_identity_socials_history_and_cache(http_get):
    cached = None

    async def cache_get(*_args, **_kwargs):
        return cached

    async def cache_set(_key, value, **_kwargs):
        nonlocal cached
        cached = value

    with (
        patch("app.services.team.cache.get", side_effect=cache_get),
        patch("app.services.team.cache.set", side_effect=cache_set),
        patch(
            "httpx.AsyncClient.get",
            side_effect=http_get(_team_pages(), fallback=(FIXTURE_DIR / "team_624_completed_empty.html").read_bytes()),
        ) as get,
    ):
        result = await team.get_team_data(TEAM_ID, completed_pages=0)
        get.reset_mock()
        get.side_effect = AssertionError("a cache hit must not fetch VLR")
        cached_result = await team.get_team_data(TEAM_ID, completed_pages=0)

    assert result.name == "Paper Rex"
    assert result.twitter == "https://x.com/pprxteam"
    assert str(result.website) == "https://pprx.team/"
    assert len(result.completed) == 100
    assert len({match.id for match in result.completed}) == 100
    assert result.completed[0].roster_core
    assert result.completed[0].opponent_roster_core
    assert cached_result == result
    get.assert_not_called()


@pytest.mark.asyncio
async def test_team_response_does_not_cache_partial_match_history(http_get):
    failures = {team.completed_matches_url(TEAM_ID, 2): 503}
    with (
        patch("app.services.team.cache.get", new=AsyncMock(return_value=None)),
        patch("app.services.team.cache.set", new=AsyncMock()) as cache_set,
        patch("httpx.AsyncClient.get", side_effect=http_get(_team_pages(), failures=failures)),
        pytest.raises(ScrapingError),
    ):
        await team.get_team_data(TEAM_ID, completed_pages=2)

    cache_set.assert_not_awaited()
