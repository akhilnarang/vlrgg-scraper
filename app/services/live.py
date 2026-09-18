"""Coordinate live match streams with Redis.

The SSE endpoint only reads snapshots from Redis. It does not request data from
VLR. Each connection registers a lease for its match IDs. The cron job writes one
shared snapshot for each active match. Live state does not use the response cache.
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from redis.asyncio import Redis

from app import constants
from app.schemas.matches import (
    Event,
    LiveCurrentMap,
    LiveMatchEvent,
    LiveSnapshot,
    LiveTeam,
    MatchData,
    MatchVideos,
    MatchWithDetails,
    Team,
    TeamWithImage,
)

logger = logging.getLogger(__name__)

# VLR reports a finished match as "final". Older snapshots may use "completed".
_TERMINAL_STATUSES = frozenset({"final", "completed"})


def new_stream_id() -> str:
    """Create an ID for one stream lease.

    :return: A unique stream ID.
    """
    return uuid4().hex


def _decode(value: bytes | str | None) -> str | None:
    """Convert one Redis value to text.

    :param value: A Redis value or ``None``.
    :return: The text value or ``None``.
    """
    return value.decode("utf-8") if isinstance(value, bytes) else value


def active_key() -> str:
    """Get the Redis key for active match IDs.

    :return: The active-set key.
    """
    return f"{constants.LIVE_REDIS_NAMESPACE}:active"


def lease_key(match_id: str) -> str:
    """Get the Redis lease key for one match.

    :param match_id: The match ID.
    :return: The lease key.
    """
    return f"{constants.LIVE_REDIS_NAMESPACE}:lease:{match_id}"


def snapshot_key(match_id: str) -> str:
    """Get the Redis snapshot key for one match.

    :param match_id: The match ID.
    :return: The snapshot key.
    """
    return f"{constants.LIVE_REDIS_NAMESPACE}:snapshot:{match_id}"


def test_counter_key() -> str:
    """Get the Redis key for the synthetic score counter.

    :return: The synthetic counter key.
    """
    return f"{constants.LIVE_REDIS_NAMESPACE}:test:counter"


def _is_terminal(status: str | None) -> bool:
    """Report whether one parser status is terminal.

    :param status: The parser status, or ``None``.
    :return: ``True`` for ``final`` and ``completed``.
    """
    return (status or "").strip().casefold() in _TERMINAL_STATUSES


def _expiry_ms(now: datetime | None = None) -> int:
    """Calculate the end time for a new lease.

    :param now: The current UTC time. The function gets the time if this is ``None``.
    :return: The lease end time in Unix milliseconds.
    """
    moment = now or datetime.now(tz=UTC)
    return int(moment.timestamp() * 1000) + constants.LIVE_LEASE_TTL * 1000


async def register(client: Redis, stream_id: str, match_ids: list[str]) -> None:
    """Register one stream as a watcher of each match.

    The function clears a stale synthetic feed before a new watcher starts when
    no other watcher survives.

    :param client: The Redis client for live state.
    :param stream_id: The unique ID for the stream.
    :param match_ids: The match IDs that the stream watches.
    :return: None.
    :raises RedisError: If Redis cannot register the leases.
    """
    if constants.LIVE_TEST_MATCH_ID in match_ids:
        cutoff = int(datetime.now(tz=UTC).timestamp() * 1000)
        check = client.pipeline(transaction=False)
        check.zremrangebyscore(lease_key(constants.LIVE_TEST_MATCH_ID), "-inf", cutoff)
        check.zcard(lease_key(constants.LIVE_TEST_MATCH_ID))
        results = await check.execute()
        if results[-1] == 0:
            await reset_test_feed(client)

    expiry = _expiry_ms()
    pipe = client.pipeline(transaction=False)
    pipe.sadd(active_key(), *match_ids)
    pipe.expire(active_key(), constants.LIVE_LEASE_KEY_TTL)
    for match_id in match_ids:
        pipe.zadd(lease_key(match_id), {stream_id: expiry})
        pipe.expire(lease_key(match_id), constants.LIVE_LEASE_KEY_TTL)
    await pipe.execute()


async def renew(client: Redis, stream_id: str, match_ids: list[str]) -> None:
    """Extend the leases for one connected stream.

    :param client: The Redis client for live state.
    :param stream_id: The unique ID for the stream.
    :param match_ids: The match IDs that the stream watches.
    :return: None.
    :raises RedisError: If Redis cannot extend the leases.
    """
    await register(client, stream_id, match_ids)


async def release(client: Redis, stream_id: str, match_ids: list[str]) -> None:
    """Remove one stream from each match lease.

    This function does not remove other streams. It resets the synthetic feed when
    the last synthetic-feed stream stops.

    :param client: The Redis client for live state.
    :param stream_id: The unique ID for the stream.
    :param match_ids: The match IDs that the stream watched.
    :return: None.
    :raises RedisError: If Redis cannot remove the leases.
    """
    pipe = client.pipeline(transaction=False)
    for match_id in match_ids:
        pipe.zrem(lease_key(match_id), stream_id)
    watches_test_feed = constants.LIVE_TEST_MATCH_ID in match_ids
    if watches_test_feed:
        pipe.zcard(lease_key(constants.LIVE_TEST_MATCH_ID))
    results = await pipe.execute()
    if watches_test_feed and results[-1] == 0:
        await reset_test_feed(client)


async def active_match_ids(client: Redis, now: datetime | None = None) -> list[str]:
    """Remove expired leases and get the active match IDs.

    :param client: The Redis client for live state.
    :param now: The current UTC time. The function gets the time if this is ``None``.
    :return: The sorted match IDs that have at least one active lease.
    :raises RedisError: If Redis cannot read or update the leases.
    """
    raw_ids = await client.smembers(active_key())  # type: ignore
    match_ids = sorted(match_id for value in raw_ids if (match_id := _decode(value)))
    if not match_ids:
        return []

    cutoff = int((now or datetime.now(tz=UTC)).timestamp() * 1000)
    pipe = client.pipeline(transaction=False)
    for match_id in match_ids:
        pipe.zremrangebyscore(lease_key(match_id), "-inf", cutoff)
        pipe.zcard(lease_key(match_id))
    results = await pipe.execute()

    active: list[str] = []
    stale: list[str] = []
    for match_id, count in zip(match_ids, results[1::2]):
        (active if count else stale).append(match_id)

    if stale:
        pipe = client.pipeline(transaction=False)
        pipe.srem(active_key(), *stale)
        for match_id in stale:
            pipe.delete(lease_key(match_id))
        if constants.LIVE_TEST_MATCH_ID in stale:
            pipe.delete(snapshot_key(constants.LIVE_TEST_MATCH_ID), test_counter_key())
        await pipe.execute()

    return active


async def read_snapshots(client: Redis, match_ids: list[str]) -> dict[str, LiveSnapshot | None]:
    """Read the latest snapshots in one Redis request.

    :param client: The Redis client for live state.
    :param match_ids: The match IDs to read.
    :return: A snapshot or ``None`` for each match ID.
    :raises RedisError: If Redis cannot read the snapshots.
    """
    values = await client.mget([snapshot_key(match_id) for match_id in match_ids])
    snapshots: dict[str, LiveSnapshot | None] = {}
    for match_id, value in zip(match_ids, values):
        raw = _decode(value)
        if not raw:
            snapshots[match_id] = None
            continue
        try:
            snapshots[match_id] = LiveSnapshot.model_validate_json(raw)
        except ValidationError:
            # Ignore one invalid snapshot. Continue to read the other snapshots.
            logger.warning("discarding unreadable live snapshot for match %s", match_id, exc_info=True)
            snapshots[match_id] = None
    return snapshots


async def store_snapshot(
    client: Redis,
    match_id: str,
    data: MatchWithDetails,
    now: datetime | None = None,
) -> LiveSnapshot:
    """Write one full match snapshot to Redis.

    Redis keeps the previous value if this write fails.

    :param client: The Redis client for live state.
    :param match_id: The match ID for the snapshot.
    :param data: The full match data.
    :param now: The observation time. The function gets the time if this is ``None``.
    :return: The snapshot that Redis stores.
    :raises RedisError: If Redis cannot write the snapshot.
    """
    moment = now or datetime.now(tz=UTC)
    snapshot = LiveSnapshot(
        match_id=match_id,
        version=int(moment.timestamp() * 1000),
        observed_at=moment,
        data=data,
    )
    await client.set(snapshot_key(match_id), snapshot.model_dump_json(), ex=constants.LIVE_SNAPSHOT_TTL)
    return snapshot


def project_live_event(snapshot: LiveSnapshot) -> LiveMatchEvent:
    """Build the compact live update for one snapshot.

    The update keeps only what a mobile Live Activity shows: team names, logos,
    series scores, and the current map scores. Team order follows the match
    header order.

    :param snapshot: The full match snapshot from Redis.
    :return: The compact live event.
    :raises ValidationError: If the compact payload is invalid.
    """
    data = snapshot.data
    terminal = _is_terminal(data.event.status)
    return LiveMatchEvent(
        match_id=snapshot.match_id,
        version=snapshot.version,
        observed_at=snapshot.observed_at,
        terminal=terminal,
        teams=[LiveTeam(name=team.name, img=team.img, score=team.score) for team in data.teams],
        current_map=_current_map(data, terminal),
    )


def _eligible_maps(data: MatchWithDetails) -> list[MatchData]:
    """Keep maps that can be the current map.

    The live parser already drops the aggregate ``all`` entry. This function
    drops unnamed and TBD entries.

    :param data: The full match data.
    :return: The maps with a real name.
    """
    return [
        map_data for map_data in data.data if map_data.map.strip() and map_data.map.strip().lower() != constants.TBD
    ]


def _map_started(map_data: MatchData) -> bool:
    """Report whether a map has started.

    A 0-0 map without rounds has no evidence and is not started.

    :param map_data: The map data.
    :return: ``True`` if the map has rounds or a nonzero score.
    """
    return bool(map_data.rounds) or any((team.score or 0) > 0 for team in map_data.teams)


def _select_current_map(data: MatchWithDetails, terminal: bool) -> MatchData | None:
    """Choose the one current map.

    An active match uses the latest started map, or the first map during the
    0-0 opening. A completed match uses the last started map.

    :param data: The full match data.
    :param terminal: Whether the match is complete.
    :return: The selected map, or ``None`` if the match has no eligible map.
    """
    maps = _eligible_maps(data)
    if not maps:
        return None

    started = [map_data for map_data in maps if _map_started(map_data)]
    if started:
        return started[-1]
    return maps[-1] if terminal else maps[0]


def _aligned_map_scores(map_data: MatchData, teams: list[TeamWithImage]) -> list[int | None]:
    """Align the selected-map scores to the top-level team order.

    The map card can list the teams in a different order than the match header.
    This function matches by stripped, case-folded name. A name that matches zero
    or more than one map team gives ``None``.

    :param map_data: The selected map.
    :param teams: The top-level teams in header order.
    :return: One score for each top-level team.
    """
    scores_by_name: dict[str, list[int | None]] = {}
    for team in map_data.teams:
        scores_by_name.setdefault(team.name.strip().casefold(), []).append(team.score)

    aligned: list[int | None] = []
    for team in teams:
        candidates = scores_by_name.get(team.name.strip().casefold(), [])
        aligned.append(candidates[0] if len(candidates) == 1 else None)
    return aligned


def _current_map(data: MatchWithDetails, terminal: bool) -> LiveCurrentMap | None:
    """Build the compact current-map field.

    :param data: The full match data.
    :param terminal: Whether the match is complete.
    :return: The current map, or ``None`` if the match has no eligible map or two teams.
    """
    selected = _select_current_map(data, terminal)
    if selected is None or len(data.teams) != 2:
        return None
    return LiveCurrentMap(name=selected.map, scores=_aligned_map_scores(selected, data.teams))


async def next_test_match(client: Redis) -> MatchWithDetails:
    """Create the next match data for the synthetic feed.

    :param client: The Redis client that stores the score counter.
    :return: The synthetic match data for the next cron tick.
    :raises RedisError: If Redis cannot increment the counter.
    """
    tick = int(await client.incr(test_counter_key()))
    phase = min(tick, 4)
    completed = phase == 4
    series_scores = [(0, 0), (0, 0), (1, 0), (2, 0)]
    map_one_scores = [(4, 2), (9, 7), (13, 9), (13, 9)]
    alpha_score, beta_score = series_scores[phase - 1]
    image = "https://www.vlr.gg/img/vlr/logo_header.png"
    teams = [
        TeamWithImage(id="test-alpha", name="SSE Test Alpha", score=alpha_score, img=image),
        TeamWithImage(id="test-beta", name="SSE Test Beta", score=beta_score, img=image),
    ]
    return MatchWithDetails(
        teams=teams,
        bans=[],
        event=Event(
            id=constants.LIVE_TEST_MATCH_ID,
            img=image,
            series="Synthetic live feed",
            stage="Final" if completed else f"Update {phase}",
            status="completed" if completed else "live",
        ),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=2 if phase >= 3 else 1,
        data=_test_maps(teams, phase, map_one_scores[phase - 1]),
        previous_encounters=[],
    )


def _test_maps(teams: list[TeamWithImage], phase: int, map_one_score: tuple[int, int]) -> list[MatchData]:
    """Create the map scores for one synthetic-feed phase.

    :param teams: The two synthetic teams.
    :param phase: The current synthetic-feed phase.
    :param map_one_score: The score for the first map.
    :return: One or two synthetic map results.
    """
    maps = [
        MatchData(
            map="Haven",
            teams=[
                Team(name=teams[0].name, score=map_one_score[0]),
                Team(name=teams[1].name, score=map_one_score[1]),
            ],
            members=[],
            rounds=[],
        )
    ]
    if phase >= 3:
        map_two_score = (5, 4) if phase == 3 else (13, 10)
        maps.append(
            MatchData(
                map="Ascent",
                teams=[
                    Team(name=teams[0].name, score=map_two_score[0]),
                    Team(name=teams[1].name, score=map_two_score[1]),
                ],
                members=[],
                rounds=[],
            )
        )
    return maps


async def reset_test_feed(client: Redis) -> None:
    """Delete the snapshot and counter for the synthetic feed.

    :param client: The Redis client for live state.
    :return: None.
    :raises RedisError: If Redis cannot delete the state.
    """
    pipe = client.pipeline(transaction=False)
    pipe.delete(snapshot_key(constants.LIVE_TEST_MATCH_ID), test_counter_key())
    await pipe.execute()
