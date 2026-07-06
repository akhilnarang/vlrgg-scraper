import json
from enum import Enum
from typing import Any

import pytest
from pydantic import BaseModel

from app.services import events, matches, news, player, standings, team


pytestmark = pytest.mark.live_golden


def serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.model_dump_json())
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, Enum):
        return value.value
    return value


def strip_relative_eta(value: Any) -> Any:
    if isinstance(value, list):
        return [strip_relative_eta(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_relative_eta(item) for key, item in value.items() if key != "eta"}
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "loader"),
    [
        ("event_2283", lambda: events.get_event_by_id("2283", client=None)),
        ("event_2760", lambda: events.get_event_by_id("2760", client=None)),
        ("event_2842", lambda: events.get_event_by_id("2842", client=None)),
        ("event_2863", lambda: events.get_event_by_id("2863", client=None)),
        ("event_2974", lambda: events.get_event_by_id("2974", client=None)),
        ("match_12345", lambda: matches.match_by_id("12345", redis_client=None)),
        ("match_673178", lambda: matches.match_by_id("673178", redis_client=None)),
        ("match_706763", lambda: matches.match_by_id("706763", redis_client=None)),
        ("match_542272", lambda: matches.match_by_id("542272", redis_client=None)),
        ("team_624", lambda: team.get_team_data("624", completed_pages=1)),
        ("team_1120", lambda: team.get_team_data("1120", completed_pages=1)),
        ("player_45", lambda: player.get_player_data("45", match_pages=1)),
        ("player_3520", lambda: player.get_player_data("3520", match_pages=1)),
        ("player_4521", lambda: player.get_player_data("4521", match_pages=1)),
        ("standings_2021", lambda: standings.standings_list(2021)),
        ("news_562934", lambda: news.news_by_id("562934")),
        ("news_562952", lambda: news.news_by_id("562952")),
    ],
)
async def test_live_vlr_parser_golden(case_name, loader, data_regression):
    parsed = await loader()
    data_regression.check(strip_relative_eta(serialize(parsed)), basename=case_name)
