from pathlib import Path
from unittest.mock import patch

import pytest

from app import constants
from app.services import player

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PLAYER_ID = "45"


def _player_pages():
    return {
        constants.PLAYER_URL.format(PLAYER_ID): (FIXTURE_DIR / "player_45.html").read_bytes(),
        **{
            player.player_matches_url(PLAYER_ID, page): (
                FIXTURE_DIR / f"player_matches_45_page{page}.html"
            ).read_bytes()
            for page in range(1, 5)
        },
    }


@pytest.mark.asyncio
async def test_player_response_includes_identity_stats_history_and_cache(http_get):
    cached = None

    async def cache_get(*_args, **_kwargs):
        return cached

    async def cache_set(_key, value, **_kwargs):
        nonlocal cached
        cached = value

    with (
        patch("app.services.player.cache.get", side_effect=cache_get),
        patch("app.services.player.cache.set", side_effect=cache_set),
        patch(
            "httpx.AsyncClient.get",
            side_effect=http_get(_player_pages(), fallback=(FIXTURE_DIR / "player_matches_45_empty.html").read_bytes()),
        ) as get,
    ):
        result = await player.get_player_data(PLAYER_ID, match_pages=0)
        get.reset_mock()
        get.side_effect = AssertionError("a cache hit must not fetch VLR")
        cached_result = await player.get_player_data(PLAYER_ID, match_pages=0)

    assert result.alias == "SicK"
    assert result.name == "Hunter Mims"
    assert result.twitter == "https://x.com/SicK_cs"
    assert result.agents[0].name
    assert result.agents[0].fkpr == pytest.approx(result.agents[0].fk / result.agents[0].rounds, abs=0.01)
    assert len(result.matches) == 158
    assert len({match.id for match in result.matches}) == 158
    assert result.matches[0].roster_core
    assert result.matches[0].opponent_roster_core
    assert cached_result == result
    get.assert_not_called()
