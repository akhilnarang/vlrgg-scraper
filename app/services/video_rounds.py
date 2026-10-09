"""Persist round winners from consecutive verified broadcast scores."""

import logging

from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import WatchError

from app import constants
from app.schemas.matches import MatchWithDetails, Round, VideoScore
from app.utils import is_final, video_lead_plausible

logger = logging.getLogger(__name__)


class VideoRounds(BaseModel):
    observed_at: int = 0
    scores: dict[str, int] = Field(default_factory=dict)
    winners: list[str | None] = Field(default_factory=list)


def _read(data: bytes | str | None) -> VideoRounds | None:
    if not isinstance(data, (bytes, str)):
        return None
    try:
        return VideoRounds.model_validate_json(data)
    except ValidationError:
        return None


def _team_ids(detail: MatchWithDetails) -> dict[str, str]:
    ids = {team.name.strip().casefold(): team.id for team in detail.teams if team.id}
    return ids if len(ids) == 2 and len(set(ids.values())) == 2 else {}


def _advance(history: VideoRounds | None, video: VideoScore, detail: MatchWithDetails) -> VideoRounds | None:
    ids = _team_ids(detail)
    pair = video.resolve_pair([(team.name, team.tag) for team in detail.teams])
    current_map = next((item for item in detail.data if item.number == video.map_number), None)
    if not ids or pair is None or current_map is None:
        return None
    scores = {ids[team.name.strip().casefold()]: entry.score for team, entry in zip(detail.teams, pair, strict=True)}
    if history is None:
        baseline = {
            ids[team.name.strip().casefold()]: team.score
            for team in current_map.teams
            if team.name.strip().casefold() in ids
        }
        if baseline.keys() != scores.keys() or any(
            score is None or score > scores[team_id] for team_id, score in baseline.items()
        ):
            baseline = {team_id: 0 for team_id in scores}
        history = VideoRounds(scores={team_id: score for team_id, score in baseline.items() if score is not None})
    if history.scores.keys() != scores.keys() or video.observed_at < history.observed_at:
        return None
    count = sum(scores.values())
    names = [ids.get(team.name.strip().casefold()) for team in current_map.teams]
    order = (
        {constants.RoundWinner.TEAM1: names[0], constants.RoundWinner.TEAM2: names[1]}
        if len(names) == 2 and all(names)
        else {}
    )
    rendered = {
        item.round_number: team_id
        for item in current_map.rounds
        if 1 <= item.round_number <= count and (team_id := order.get(item.winner)) is not None
    }
    winners = list(history.winners)
    baseline = dict(history.scores)
    if any(score < history.scores[team_id] for team_id, score in scores.items()):
        # A lower verified reading corrects a misread score or restarts the map: rebase the
        # history on it instead of pinning the stale higher score and its inferred winners.
        winners = winners[:count]
        differences = dict.fromkeys(scores, 0)
    else:
        for number, team_id in rendered.items():
            if number <= len(winners) and (previous := winners[number - 1]) not in (None, team_id):
                # VLR's rendered winner is authoritative over the tracker's inference: the round's
                # point moves teams, so the next single-round gain is credited to the right team.
                baseline[previous] -= 1
                baseline[team_id] += 1
                winners[number - 1] = team_id
        differences = {team_id: score - baseline[team_id] for team_id, score in scores.items()}
    winners = winners[:count] + [None] * max(0, count - len(winners))
    if sum(differences.values()) == 1:
        winner = next((team_id for team_id, change in differences.items() if change == 1), None)
        if winner is not None:
            winners[count - 1] = winner
    for number, team_id in rendered.items():
        # VLR's rendered winner is authoritative and also fills rounds the tracker never inferred.
        winners[number - 1] = team_id
    for team_id, score in scores.items():
        # A corrected or contradicted reading never leaves a team credited with more rounds than it scored.
        excess = winners.count(team_id) - score
        for index in reversed(range(count)):
            if excess <= 0:
                break
            if winners[index] == team_id:
                winners[index] = None
                excess -= 1
    return VideoRounds(observed_at=video.observed_at, scores=scores, winners=winners)


async def store_score(
    client: Redis,
    video: VideoScore,
    resolved: tuple[str, MatchWithDetails, dict[str, int]] | None,
) -> tuple[VideoScore | None, bool]:
    """Commit the score and its winner history before scheduling any push.

    :param client: Redis client.
    :param video: Incoming tracker observation.
    :param resolved: Cached match and team scores matched to the observation.
    :return: Previous observation and whether the push content changed.
    """
    key = (
        constants.VIDEO_ROUNDS_KEY.format(resolved[0], video.map_number)
        if resolved is not None and _team_ids(resolved[1])
        else None
    )
    async with client.pipeline(transaction=True) as pipe:
        while True:
            try:
                await pipe.watch(constants.VIDEO_SCORE_KEY, *([key] if key else []))
                previous = VideoScore.from_cache(await pipe.get(constants.VIDEO_SCORE_KEY))
                if previous is not None and video.observed_at < previous.observed_at:
                    await pipe.unwatch()
                    return previous, False
                history = None
                old_history = None
                if key is not None and resolved is not None and video.healthy:
                    old_history = _read(await pipe.get(key))
                    history = _advance(old_history, video, resolved[1])
                    if history is None:
                        await pipe.unwatch()
                        return previous, False
                changed = previous is None or not previous.same_score(video)
                if history is not None:
                    changed = changed or old_history is None or history.winners != old_history.winners
                pipe.multi()
                pipe.set(constants.VIDEO_SCORE_KEY, video.model_dump_json(), ex=constants.VIDEO_SCORE_TTL)
                if key is not None and history is not None:
                    pipe.set(key, history.model_dump_json(), ex=constants.VIDEO_ROUNDS_TTL)
                await pipe.execute()
                return previous, changed
            except WatchError:
                continue


async def apply_history(client: Redis, match_id: str, detail: MatchWithDetails) -> None:
    """Overlay tracker winners on fetched or cached details without fetching VLR.

    :param client: Redis client.
    :param match_id: Match owning the stored round histories.
    :param detail: Details updated in place before push projection.
    """
    ids = _team_ids(detail)
    if not ids:
        return
    for current_map in detail.data:
        history = _read(await client.get(constants.VIDEO_ROUNDS_KEY.format(match_id, current_map.number)))
        if history is None or history.scores.keys() != set(ids.values()):
            continue
        names = [ids.get(team.name.strip().casefold()) for team in current_map.teams]
        if len(names) != 2 or not all(names):
            continue
        if not video_lead_plausible((team.score for team in current_map.teams), history.scores.values()):
            logger.warning(
                "ignoring tracker history for match %s map %s: more than %s rounds ahead of VLR",
                match_id,
                current_map.number,
                constants.VIDEO_MAX_LEAD_ROUNDS,
            )
            continue
        existing = {item.round_number: item for item in current_map.rounds}
        final_count = sum(team.score or 0 for team in current_map.teams) if is_final(detail.event.status) else None
        for number, team_id in enumerate(history.winners, start=1):
            if final_count is not None and number > final_count:
                continue
            winner = (
                constants.RoundWinner.TEAM1
                if team_id == names[0]
                else constants.RoundWinner.TEAM2
                if team_id == names[1]
                else ""
            )
            if number in existing:
                # VLR's own winner stands; the tracker only fills rounds VLR has not decided.
                if not existing[number].winner and winner:
                    existing[number] = existing[number].model_copy(update={"winner": winner})
            else:
                # Keep every completed round's position, so trailing unknown rounds stay null
                # in the projection instead of collapsing the list.
                existing[number] = Round(
                    round_number=number, round_score="", winner=winner, side="", win_type="Unknown"
                )
        current_map.rounds = [existing[number] for number in sorted(existing)]
        if not is_final(detail.event.status):
            for team in current_map.teams:
                team.score = max(team.score or 0, history.scores[ids[team.name.strip().casefold()]])
