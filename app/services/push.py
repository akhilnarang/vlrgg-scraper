"""Projection helpers for compact live-match push state."""

import json
import logging
import time
from typing import NamedTuple

from redis.asyncio import Redis

from app import constants
from app.constants import (
    MAP_WIN_ROUNDS,
    NA,
    PUSH_DETAILS_KEY,
    TBD,
    VIDEO_SCORE_KEY,
    VIDEO_STALE_SECONDS,
    Platform,
    TeamSide,
)
from app.db.models import DeviceToken
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import (
    CompactState,
    MatchData,
    MatchWithDetails,
    PushCurrentMap,
    PushMapRounds,
    PushPause,
    PushTeam,
    VideoScore,
)
from app.utils import is_final, is_live, video_lead_plausible

logger = logging.getLogger(__name__)


class Routing(NamedTuple):
    """IDs that route one match's updates to follower tokens."""

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
    """Extract match, event, team, and player IDs for follower routing.

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


async def video_score(client: Redis) -> VideoScore | None:
    """Read the score the broadcast video tracker last stored.

    :param client: Redis client.
    :return: The video score, or None when there is none or it is invalid.
    """
    return VideoScore.from_cache(await client.get(VIDEO_SCORE_KEY))


async def cached_details(client: Redis) -> dict[str, dict]:
    """Read the match details the last cron run kept.

    :param client: Redis client.
    :return: Each match's details as JSON-ready data, keyed by match ID.
    """
    data = await client.get(PUSH_DETAILS_KEY)
    return json.loads(data) if data else {}


def project_state(match_id: str, detail: MatchWithDetails, video: VideoScore | None = None) -> CompactState | None:
    """Project match details into a compact score state.

    :param match_id: Match identifier.
    :param detail: Scraped match details.
    :param video: Latest tracker score, or None.
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
        stage=detail.event.stage or None,
        teams=[
            PushTeam(id=team.id, name=team.name, tag=team.tag, img=team.img, score=team.score) for team in detail.teams
        ],
        current_map=current,
        map_winners=_map_winners(detail),
        map_round_winners=_map_round_winners(detail),
        pause=_pause(video, current),
    )


def _pause(video: VideoScore | None, current: PushCurrentMap | None) -> PushPause | None:
    """Extract an active pause matching the displayed map.

    Only the kind and reason are projected: the tracker's ``since`` is on its own
    clock, and the state's absolute ``observed_at`` dates the pause instead.

    A tracker that has started a map VLR has not rendered yet still carries its pause, so a pause
    during a map opening is not lost while the projection still shows the previous map. A pause from
    a map the tracker is behind on is dropped, and so is one from a tracker that has gone quiet: the
    score it left behind keeps VLR's floor, but it must not keep a pause shown.

    :param video: Latest tracker score, or None.
    :param current: Currently displayed map, or None.
    :return: Active pause payload, or None.
    """
    if video is None or video.pause is None or current is None or current.number > video.map_number:
        return None
    if time.time() - video.observed_at > VIDEO_STALE_SECONDS:
        return None
    return PushPause(kind=video.pause.kind, reason=video.pause.reason)


def match_codes(detail: MatchWithDetails, codes: list[str]) -> bool:
    """Check whether candidate codes unambiguously match the two teams.

    :param detail: Scraped match details.
    :param codes: Candidate team codes.
    :return: True if each code uniquely matches a distinct team.
    """
    if len(detail.teams) != 2 or len(codes) != 2:
        return False
    wanted = [code.strip().casefold() for code in codes]
    if not all(wanted) or wanted[0] == wanted[1]:
        return False
    owners = [
        {
            index
            for index, team in enumerate(detail.teams)
            if code in {(value or "").strip().casefold() for value in (team.tag, team.name)} - {""}
        }
        for code in wanted
    ]
    return len(owners[0]) == len(owners[1]) == 1 and owners[0] != owners[1]


def video_team_scores(detail: MatchWithDetails, video: VideoScore) -> dict[str, int] | None:
    """Match the video's teams to the match's, by VLR name or tag.

    :param detail: Scraped match details.
    :param video: Score read from the broadcast video.
    :return: Each team's video score keyed by its casefolded VLR name, or None when the video is of another match.
    """
    if len(detail.teams) != 2 or _map_with_number(detail, video.map_number) is None:
        return None
    resolved = video.resolve_pair([(team.name, team.tag) for team in detail.teams])
    if resolved is None:
        return None
    return {team.name.strip().casefold(): entry.score for team, entry in zip(detail.teams, resolved, strict=True)}


async def resolve_video_match(client: Redis, video: VideoScore) -> tuple[str, MatchWithDetails, dict[str, int]] | None:
    """Find the cached match matching the stored video score.

    :param client: Redis client.
    :param video: Stored tracker score.
    :return: Tuple of match ID, details, and team scores, or None if unmatched.
    """
    return resolve_video_match_in(await cached_details(client), video)


def resolve_video_match_in(
    details: dict[str, dict], video: VideoScore
) -> tuple[str, MatchWithDetails, dict[str, int]] | None:
    """Find the match matching the stored video score among the given details.

    :param details: Each candidate match's details as JSON-ready data, keyed by match ID.
    :param video: Stored tracker score.
    :return: Tuple of match ID, details, and team scores, or None if unmatched.
    """
    candidates = [(match_id, MatchWithDetails.model_validate(cached)) for match_id, cached in details.items()]
    matched = [
        (match_id, detail, scores)
        for match_id, detail in candidates
        if (scores := video_team_scores(detail, video)) is not None
    ]
    return _preferred_match(matched)


async def resolve_video_match_with_fallback(
    client: Redis, video: VideoScore
) -> tuple[str, MatchWithDetails, dict[str, int]] | None:
    """Find the match matching the stored video score, from the cache or its stored SQLite details.

    The SQLite details outlive the Redis cache, so a restart still resolves the match and its round
    history advances instead of losing the new winners.

    :param client: Redis client.
    :param video: Stored tracker score.
    :return: Tuple of match ID, details, and team scores, or None if unmatched.
    """
    if (resolved := await resolve_video_match(client, video)) is not None:
        return resolved
    from app.core import connections
    from app.services import live_store

    sessions = connections.subscription_sessions
    if sessions is None:
        return None
    async with sessions() as session:
        return resolve_video_match_in(await live_store.live_details(session), video)


async def resolve_video_match_by_codes(client: Redis, codes: list[str]) -> tuple[str, MatchWithDetails] | None:
    """Find the cached match matching candidate team codes.

    :param client: Redis client.
    :param codes: Two Riot team codes.
    :return: Tuple of match ID and details, or None if ambiguous or unmatched.
    """
    candidates = [
        (match_id, MatchWithDetails.model_validate(cached))
        for match_id, cached in (await cached_details(client)).items()
    ]
    return _preferred_match([candidate for candidate in candidates if match_codes(candidate[1], codes)])


def _preferred_match[T: tuple[str, MatchWithDetails] | tuple[str, MatchWithDetails, dict[str, int]]](
    candidates: list[T],
) -> T | None:
    """Return the sole live candidate, or sole candidate when none are live.

    :param candidates: Candidate matches as ``(match_id, details[, ...])`` tuples.
    :return: Preferred candidate tuple, or None if ambiguous.
    """
    live = [candidate for candidate in candidates if is_live(candidate[1].event.status)]
    preferred = live or candidates
    return preferred[0] if len(preferred) == 1 else None


def represents_video(detail: MatchWithDetails, video: VideoScore, scores: dict[str, int]) -> bool:
    """Check whether the projection displays the video's map with at least its scores.

    :param detail: Scraped match details, after :func:`raise_map_scores`.
    :param video: Score read from the broadcast video.
    :param scores: Each team's video score keyed by casefolded VLR name, from :func:`video_team_scores`.
    :return: True if the projection's map is the video's and no team shows below its video score.
    """
    current = _current_map(detail, is_final(detail.event.status))
    if current is None or current.number != video.map_number:
        return False
    displayed = dict(zip((team.name.strip().casefold() for team in detail.teams), current.scores, strict=True))
    return all((shown := displayed.get(name)) is not None and shown >= score for name, score in scores.items())


def raise_map_scores(detail: MatchWithDetails, map_number: int, scores: dict[str, int]) -> None:
    """Raise each team's score on a map to the video's where the video is ahead, so neither source lowers it.

    :param detail: Scraped match details, updated in place.
    :param map_number: The video's map, counted from 1.
    :param scores: Each team's video score keyed by casefolded VLR name, from :func:`video_team_scores`.
    :return: None.
    """
    if (map_data := _map_with_number(detail, map_number)) is None:
        return
    if not video_lead_plausible((team.score for team in map_data.teams), scores.values()):
        logger.warning(
            "ignoring video score %s on map %s: more than %s rounds ahead of VLR",
            scores,
            map_number,
            constants.VIDEO_MAX_LEAD_ROUNDS,
        )
        return
    for team in map_data.teams:
        if (score := scores.get(team.name.strip().casefold())) is not None:
            team.score = max(team.score or 0, score)


def order_teams_for_broadcast(detail: MatchWithDetails, video: VideoScore) -> None:
    """Order match teams blue-first when broadcast sides match the active map.

    :param detail: Scraped match details, reordered in place.
    :param video: Score read from the broadcast video.
    :return: None.
    """
    current = _current_map(detail, is_final(detail.event.status))
    if current is None or current.number != video.map_number:
        return
    resolved = video.resolve_pair([(team.name, team.tag) for team in detail.teams])
    if resolved is not None and [entry.side for entry in resolved] == [TeamSide.RED, TeamSide.BLUE]:
        detail.teams.reverse()


def final_from_last_sent(last_state_json: str) -> CompactState:
    """Rebuild the last sent score as a final state.

    :param last_state_json: Stored compact state, saved without its observation time.
    :return: The same score, marked terminal and observed now.
    """
    return CompactState.model_validate(
        {**json.loads(last_state_json), "terminal": True, "observed_at": int(time.time())}
    )


def _map_with_number(detail: MatchWithDetails, map_number: int) -> MatchData | None:
    """Find the entry VLR rendered for a series game number.

    :param detail: Scraped match details.
    :param map_number: Game number in the series, counted from 1.
    :return: The map entry, or None when VLR has no stats panel for that game.
    """
    return next((item for item in detail.data if item.number == map_number), None)


def _named_maps(detail: MatchWithDetails) -> list[MatchData]:
    """List the match's maps that have a name.

    :param detail: Scraped match details.
    :return: Maps in series order, without unnamed or TBD entries.
    """
    return [item for item in detail.data if _has_map_name(item.map)]


def _has_map_name(name: str) -> bool:
    """Return whether a map name is non-empty and not a placeholder.

    :param name: Map name from VLR.
    :return: True if the name identifies a known map.
    """
    return name.strip().casefold() not in {"", TBD, NA}


def _map_slots(detail: MatchWithDetails) -> int:
    """Count the match's map slots.

    :param detail: Scraped match details.
    :return: Number of series game slots, whichever is larger of the series length and the last game VLR rendered.
    """
    return max(detail.total_maps, max((item.number for item in detail.data), default=0))


def ordered_map_names(detail: MatchWithDetails) -> list[str]:
    """List map names by series slot, using empty strings for unpopulated slots.

    :param detail: Scraped match details.
    :return: Map names in series order with empty strings for unnamed or TBD maps.
    """
    names = [""] * _map_slots(detail)
    for item in detail.data:
        if _has_map_name(item.map):
            names[item.number - 1] = item.map
    return names


def _current_map(detail: MatchWithDetails, terminal: bool) -> PushCurrentMap | None:
    """Find the map to display in a compact score state.

    :param detail: Scraped match details.
    :param terminal: Whether the match is final.
    :return: Selected map, or None when no map is available.
    """
    maps = _named_maps(detail)
    if not maps:
        return None
    started = [item for item in maps if item.rounds or any((team.score or 0) > 0 for team in item.teams)]
    selected = started[-1] if started else (maps[-1] if terminal else maps[0])
    return PushCurrentMap(name=selected.map, number=selected.number, scores=_aligned_scores(selected, detail))


def _map_winners(detail: MatchWithDetails) -> list[str | None]:
    """Find the winning team of each finished map.

    :param detail: Scraped match details.
    :return: One entry per map up to ``total_maps``: the winner's team ID, or None if not finished.
    """
    winners: list[str | None] = [None] * _map_slots(detail)
    for map_data in detail.data:
        first, second = _aligned_scores(map_data, detail)
        if first is None or second is None:
            continue
        # A map ends at 13 rounds with a two-round lead, which also covers overtime.
        if max(first, second) >= MAP_WIN_ROUNDS and abs(first - second) >= 2:
            winners[map_data.number - 1] = detail.teams[0 if first > second else 1].id
    return winners


def _map_round_winners(detail: MatchWithDetails) -> list[PushMapRounds]:
    """Find each map's round winners as indices into the match's team order.

    :param detail: Scraped match details.
    :return: One entry per map slot, each holding that map's winning team index (0 or 1) per round.
    """
    winners = [PushMapRounds(map_number=number) for number in range(1, _map_slots(detail) + 1)]
    order = [team.name.strip().casefold() for team in detail.teams]
    for map_data in detail.data:
        names = [team.name.strip().casefold() for team in map_data.teams]
        if sorted(names) != sorted(order):
            continue
        index = {constants.RoundWinner.TEAM1: order.index(names[0]), constants.RoundWinner.TEAM2: order.index(names[1])}
        rounds = [None] * max((item.round_number for item in map_data.rounds), default=0)
        for item in map_data.rounds:
            if item.round_number >= 1:
                rounds[item.round_number - 1] = index.get(item.winner)
        winners[map_data.number - 1] = PushMapRounds(map_number=map_data.number, winners=rounds)
    return winners


def _aligned_scores(map_data: MatchData, detail: MatchWithDetails) -> list[int | None]:
    """Align map scores with the match's team order.

    :param map_data: Selected map details.
    :param detail: Match details defining team order.
    :return: Map scores in team order.
    """
    scores = {team.name.strip().casefold(): team.score for team in map_data.teams}
    return [scores.get(team.name.strip().casefold()) for team in detail.teams]
