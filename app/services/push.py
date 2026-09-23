"""Projection helpers for compact live-match push state."""

import json
import time
from typing import NamedTuple

from app.schemas.matches import CompactState, MatchData, MatchWithDetails, PushCurrentMap, PushTeam
from app.utils import is_final


class Routing(NamedTuple):
    """IDs that route one match's updates to favorites and topics."""

    match_id: str
    event_id: str | None
    team_ids: list[str]
    player_ids: list[str]


def routing_ids(match_id: str, detail: MatchWithDetails) -> Routing:
    """Extract match, event, team, and player IDs for subscription topics.

    :param match_id: Match identifier.
    :param detail: Scraped match details.
    :return: The match routing IDs.
    """
    event_id = detail.event.id if detail.event.id.isascii() and detail.event.id.isdigit() else None
    team_ids = [team.id for team in detail.teams if team.id and team.id.isascii() and team.id.isdigit()]
    player_ids = sorted(
        {
            member.id
            for map_data in detail.data
            for member in map_data.members
            if member.id.isascii() and member.id.isdigit()
        },
        key=int,
    )
    return Routing(match_id, event_id, team_ids, player_ids)


def project_state(match_id: str, detail: MatchWithDetails) -> CompactState | None:
    """Project match details into a compact score state.

    :param match_id: Match identifier.
    :param detail: Scraped match details.
    :return: Compact state, or None for an incomplete match.
    """
    if len(detail.teams) != 2:
        return None
    terminal = is_final(detail.event.status)
    current = _current_map(detail, terminal)
    return CompactState(
        match_id=match_id,
        observed_at=int(time.time()),
        terminal=terminal,
        total_maps=detail.total_maps,
        teams=[PushTeam(name=team.name, tag=team.tag, img=team.img, score=team.score) for team in detail.teams],
        current_map=current,
    )


def final_from_last_sent(last_state_json: str) -> CompactState:
    """Rebuild the last sent score as a final state.

    :param last_state_json: Stored compact state, saved without its observation time.
    :return: The same score, marked terminal and observed now.
    """
    return CompactState.model_validate(
        {**json.loads(last_state_json), "terminal": True, "observed_at": int(time.time())}
    )


def _current_map(detail: MatchWithDetails, terminal: bool) -> PushCurrentMap | None:
    """Find the map to display in a compact score state.

    :param detail: Scraped match details.
    :param terminal: Whether the match is final.
    :return: Selected map, or None when no map is available.
    """
    maps = [item for item in detail.data if item.map.strip() and item.map.strip().casefold() != "tbd"]
    if not maps:
        return None
    started = [item for item in maps if item.rounds or any((team.score or 0) > 0 for team in item.teams)]
    selected = started[-1] if started else (maps[-1] if terminal else maps[0])
    number = maps.index(selected) + 1 if selected in maps else None
    return PushCurrentMap(name=selected.map, number=number, scores=_aligned_scores(selected, detail))


def _aligned_scores(map_data: MatchData, detail: MatchWithDetails) -> list[int | None]:
    """Align map scores with the match's team order.

    :param map_data: Selected map details.
    :param detail: Match details defining team order.
    :return: Map scores in team order.
    """
    scores = {team.name.strip().casefold(): team.score for team in map_data.teams}
    return [scores.get(team.name.strip().casefold()) for team in detail.teams]
