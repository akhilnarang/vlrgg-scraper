"""Live structural-health checks against VLR.

These cover the *windowed* endpoints -- team, player, rankings, match list --
whose pages always show "now". There is no fixed baseline to snapshot against at
any data age: a team page shows that team's latest matches, so it moves whenever
they play. Snapshot equality here can only ever produce false alarms, so these
assert structural invariants instead (see `contracts.py`).

Frozen entities -- completed matches, completed events, historical standings, old
articles -- are *not* here. Their data never changes, so they keep exact-equality
snapshot tests in `tests/golden/`, which are a strictly stronger check.

Run: `uv run pytest -m live_health tests/live`
"""

import pytest

from app.constants import MatchStatus, SearchCategory
from app.services import events, matches, news, player, rankings, search, team
from tests.live import contracts


pytestmark = [pytest.mark.live_health, pytest.mark.asyncio]

# Long-established teams, unlikely to vanish. Multiple teams so that one of them
# going inactive cannot fire the rank check on its own.
TEAM_IDS = ["624", "1120", "2", "17"]

# Six players, not two: the sample-wide checks use a 0.75 threshold, so with two
# entries a single player deleting their Twitter link puts the ratio at 0.5 and
# reds the job -- the exact churn those checks are meant to tolerate. Six leaves
# room for one to drop off (5/6 = 83%) while still firing if the parser nulls
# them all.
PLAYER_IDS = ["45", "3520", "4521", "9", "1265", "729"]


async def test_rankings_health():
    """The `zx-tab` selector missing yields `[]` with no error: the emptiest alarm."""
    result = await rankings.ranking_list()
    contracts.check_rankings(result)


@pytest.mark.parametrize("team_id", TEAM_IDS)
async def test_team_health(team_id):
    result = await team.get_team_data(team_id, completed_pages=1)
    contracts.check_team(result)


async def test_team_sample_health():
    """Checked across a sample: one team losing its rank or roster is churn, all of them is a break."""
    teams = [await team.get_team_data(team_id, completed_pages=1) for team_id in TEAM_IDS]
    contracts.check_team_ranks(teams)
    contracts.check_team_rosters(teams)
    contracts.check_team_socials(teams)


@pytest.mark.parametrize("player_id", PLAYER_IDS)
async def test_player_health(player_id):
    result = await player.get_player_data(player_id, match_pages=1)
    contracts.check_player(result)


async def test_player_sample_health():
    """Socials are only meaningful across a sample: one player may have no link."""
    players = [await player.get_player_data(player_id, match_pages=1) for player_id in PLAYER_IDS]
    contracts.check_player_socials(players)


async def test_match_list_health():
    result = await matches.match_list(redis_client=None)
    contracts.check_match_list(result, completed_status=MatchStatus.COMPLETED)


async def test_search_health():
    """`search-item` missing yields [] with no error."""
    result = await search.get_data(SearchCategory.ALL, "sentinels")
    contracts.check_search_results(result)


async def test_event_list_health():
    """`events-container-col` missing yields [] with no error."""
    result = await events.get_events(cache_client=None, pages=1)
    contracts.check_event_list(result)


async def test_news_list_health():
    """`wf-module-item` missing yields [] with no error."""
    result = await news.news_list(pages=1)
    contracts.check_news_list(result)


async def test_completed_match_health():
    """A completed match must report its scores.

    Pinned to a finished event, so the result is frozen: this is the check that
    would have caught `js-spoiler` being renamed to `sp-hide`, which silently
    nulled the score on every completed match the API served.
    """
    result = await matches.match_by_id("542272", redis_client=None)
    contracts.check_match_details(result)
