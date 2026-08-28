"""Live behavioral contracts for VLR pages whose contents change over time."""

import pytest

from app.constants import MatchStatus, SearchCategory
from app.services import events, matches, news, player, rankings, search, team
from tests.live import contracts

pytestmark = [pytest.mark.live_health, pytest.mark.asyncio]

TEAM_IDS = ["624", "1120", "2", "17"]
PLAYER_IDS = ["45", "3520", "4521", "9", "1265", "729"]

# The samples are large enough for one legitimate missing value to stay above
# the 0.75 ratio used by the cross-page contracts.


async def test_rankings_contract():
    contracts.check_rankings(await rankings.ranking_list())


async def test_team_contract():
    teams = [await team.get_team_data(team_id, completed_pages=1) for team_id in TEAM_IDS]
    for result in teams:
        contracts.check_team(result)
    contracts.check_team_ranks(teams)
    contracts.check_team_rosters(teams)
    contracts.check_team_socials(teams)


async def test_player_contract():
    players = [await player.get_player_data(player_id, match_pages=1) for player_id in PLAYER_IDS]
    for result in players:
        contracts.check_player(result)
    contracts.check_player_socials(players)


async def test_match_list_contract():
    result = await matches.match_list(redis_client=None)
    contracts.check_match_list(result, completed_status=MatchStatus.COMPLETED)


async def test_search_contract():
    contracts.check_search_results(await search.get_data(SearchCategory.ALL, "sentinels"))


async def test_event_list_contract():
    contracts.check_event_list(await events.get_events(cache_client=None, pages=1))


async def test_news_list_contract():
    contracts.check_news_list(await news.news_list(pages=1))
