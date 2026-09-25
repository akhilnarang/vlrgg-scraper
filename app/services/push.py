"""Projection helpers for compact live-match push state."""

import json
import time
from typing import NamedTuple

from app.constants import MAP_WIN_ROUNDS, Platform
from app.db.models import DeviceToken
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import CompactState, MatchData, MatchWithDetails, PushCurrentMap, PushTeam
from app.utils import is_final


class Routing(NamedTuple):
    """IDs that route one match's updates to favorites and topics."""

    match_id: str
    event_id: str | None
    team_ids: list[str]
    player_ids: list[str]


async def deliver_instant_start(
    client_id: str,
    token_row: DeviceToken,
    match_id: str,
    state: CompactState,
    channel_id: str | None,
) -> None:
    """Deliver an immediate live-match start to one registered device.

    :param client_id: Client UUID.
    :param token_row: Registered device token and platform.
    :param match_id: Match identifier.
    :param state: Current compact match state.
    :param channel_id: Existing APNs broadcast channel, if any.
    :return: None.
    :raises ServiceUnavailableError: If the platform push provider is unavailable or rejects the start.
    """
    from app.services import apns, fcm

    if token_row.platform == Platform.ANDROID:
        await fcm.deliver_start(client_id, token_row.token, state)
    else:
        await apns.deliver_start(client_id, token_row.token, match_id, state, channel_id)


async def clear_rejected_token(token: str) -> None:
    """Remove a provider-rejected token in its own transaction.

    :param token: Token rejected by APNs or FCM.
    :return: None.
    """
    from app.core import connections
    from app.services.subscription_store import SubscriptionStore

    sessions = connections.subscription_sessions
    if sessions is None:
        raise ServiceUnavailableError("Live updates are unavailable")
    async with sessions.begin() as session:
        await SubscriptionStore(session).clear_token(token)


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
        teams=[
            PushTeam(id=team.id, name=team.name, tag=team.tag, img=team.img, score=team.score) for team in detail.teams
        ],
        current_map=current,
        map_winners=_map_winners(detail),
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


def _map_winners(detail: MatchWithDetails) -> list[str | None]:
    """Find the winning team of each finished map.

    :param detail: Scraped match details.
    :return: One entry per map up to ``total_maps``: the winner's team ID, or None if not finished.
    """
    maps = [item for item in detail.data if item.map.strip() and item.map.strip().casefold() != "tbd"]
    winners: list[str | None] = [None] * max(detail.total_maps, len(maps))
    for index, map_data in enumerate(maps):
        first, second = _aligned_scores(map_data, detail)
        if first is None or second is None:
            continue
        # A map ends at 13 rounds with a two-round lead, which also covers overtime.
        if max(first, second) >= MAP_WIN_ROUNDS and abs(first - second) >= 2:
            winners[index] = detail.teams[0 if first > second else 1].id
    return winners


def _aligned_scores(map_data: MatchData, detail: MatchWithDetails) -> list[int | None]:
    """Align map scores with the match's team order.

    :param map_data: Selected map details.
    :param detail: Match details defining team order.
    :return: Map scores in team order.
    """
    scores = {team.name.strip().casefold(): team.score for team in map_data.teams}
    return [scores.get(team.name.strip().casefold()) for team in detail.teams]
