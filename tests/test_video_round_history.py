"""Fast tracker ingestion must retain round history before background pushes run."""

import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks
from redis.exceptions import WatchError

from app import constants
from app.api.v1.endpoints.video import store_video_score
from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, Round, Team, TeamWithImage, VideoScore
from app.services import push, video_rounds


class MemoryPipeline:
    """Redis transaction surface, sharing the caller's mocked storage."""

    def __init__(self, client):
        self.client = client
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def watch(self, *keys):
        self.commands = []
        self.watched = {key: await self.client.get(key) for key in keys}

    async def unwatch(self):
        pass

    async def get(self, key):
        return await self.client.get(key)

    def multi(self):
        pass

    def set(self, key, value, **kwargs):
        self.commands.append((key, value, kwargs))
        return self

    async def execute(self):
        if hook := self.client.__dict__.pop("before_execute", None):
            await hook()
        for key, previous in self.watched.items():
            if await self.client.get(key) != previous:
                raise WatchError("watched score changed")
        return [await self.client.set(key, value, **kwargs) for key, value, kwargs in self.commands]


def memory_redis(values):
    client = AsyncMock()
    client.get.side_effect = lambda key: values.get(key)

    def write(key, value, **_kwargs):
        previous, values[key] = values.get(key), value
        return previous

    client.set.side_effect = write
    client.pipeline = Mock(side_effect=lambda **_kwargs: MemoryPipeline(client))
    return client


def detail(scores=(0, 0), rounds=()):
    return MatchWithDetails(
        teams=[
            TeamWithImage(id="1", name="Alpha", tag="ALP", score=0, img="https://cdn.vlr.gg/a.png"),
            TeamWithImage(id="2", name="Beta", tag="BET", score=0, img="https://cdn.vlr.gg/b.png"),
        ],
        bans=[],
        event=Event(id="99", img="https://cdn.vlr.gg/e.png", series="Series", stage="Stage", status="live"),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=1,
        total_maps=3,
        data=[
            MatchData(
                number=1,
                map="Ascent",
                teams=[Team(name="Alpha", score=scores[0]), Team(name="Beta", score=scores[1])],
                members=[],
                rounds=list(rounds),
            )
        ],
        previous_encounters=[],
    )


def video(scores, observed_at, *, number=1, status="ok", reverse=False):
    teams = [
        {"code": "ALP", "name": "Alpha", "score": scores[0], "side": "blue"},
        {"code": "BET", "name": "Beta", "score": scores[1], "side": "red"},
    ]
    if reverse:
        teams.reverse()
        teams[0]["side"], teams[1]["side"] = "blue", "red"
    return VideoScore(status=status, observed_at=observed_at, map_number=number, teams=teams)


async def projection(client, source):
    resolved = await push.resolve_video_match(client, source)
    assert resolved is not None
    match_id, match, scores = resolved
    baselines = await video_rounds.apply_history(client, match_id, match)
    push.raise_map_scores(match, source.map_number, scores, baselines.get(source.map_number))
    push.order_teams_for_broadcast(match, source)
    state = push.project_state(match_id, match, source)
    assert state is not None and state.current_map is not None
    return state


@pytest.mark.asyncio
async def test_ingestion_pushes_new_round_before_vlr_history_catches_up():
    """9:5 must already color round14 even while cached VLR only knows eight rounds."""
    now = int(time.time()) - 10
    rounds = [
        Round(round_number=n, round_score="", winner="team1", side="attack", win_type="Elimination")
        for n in range(1, 9)
    ]
    cached = detail((8, 5), rounds)
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)
    background = BackgroundTasks()
    await store_video_score(video((8, 5), now), background, client)
    source = video((9, 5), now + 1)
    await store_video_score(source, background, client)

    # No scheduled delivery has run: ingestion itself must have retained the history.
    state = await projection(client, source)
    assert state.current_map is not None
    assert state.current_map.scores == [9, 5]
    assert state.map_round_winners[0].winners == [0] * 8 + [None] * 5 + [0]
    assert state.map_round_winners[0].scores == [9, 5]
    assert (
        json.loads(values[constants.PUSH_DETAILS_KEY])["123"]["data"][0]["rounds"]
        == cached.model_dump(mode="json")["data"][0]["rounds"]
    )


@pytest.mark.asyncio
async def test_coalesced_push_preserves_rounds_across_halftime_and_new_map():
    """Queued pushes, changing broadcast order, and map resets cannot lose earlier winners."""
    now = int(time.time()) - 10
    cached = detail()
    cached.data.append(
        MatchData(
            number=2,
            map="Lotus",
            teams=[Team(name="Alpha", score=0), Team(name="Beta", score=0)],
            members=[],
            rounds=[],
        )
    )
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)
    background = BackgroundTasks()
    for offset, scores in enumerate(((0, 0), (1, 0), (1, 1), (2, 1))):
        source = video(scores, now + offset)
        await store_video_score(source, background, client)
    source = video((2, 2), now + 4, reverse=True)
    await store_video_score(source, background, client)
    state = await projection(client, source)
    assert [team.id for team in state.teams] == ["2", "1"]
    assert state.map_round_winners[0].winners == [1, 0, 1, 0]

    source = video((0, 1), now + 5, number=2)
    await store_video_score(source, background, client)
    state = await projection(client, source)
    assert state.current_map is not None
    assert state.current_map.number == 2 and state.current_map.scores == [0, 1]
    assert [item.winners for item in state.map_round_winners] == [[0, 1, 0, 1], [1], []]


@pytest.mark.asyncio
async def test_gaps_and_untrusted_reads_do_not_invent_round_winners():
    """Skipped rounds stay unknown; stale reads and tracker errors cannot corrupt them."""
    now = int(time.time()) - 10
    cached = detail()
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    async def ingest(source):
        resolved = await push.resolve_video_match(client, source)
        return await video_rounds.store_score(client, source, resolved)

    await ingest(video((1, 0), now))
    source = video((3, 1), now + 1)
    await ingest(source)
    # The skipped rounds keep their positions as null instead of shrinking the list.
    assert (await projection(client, source)).map_round_winners[0].winners == [0, None, None, None]
    before = dict(values)
    _, changed = await ingest(video((4, 1), now))
    assert changed is False and values == before

    await ingest(video((3, 2), now + 2, status="error"))
    assert (await projection(client, source)).map_round_winners[0].winners == [0, None, None, None]
    source = video((3, 2), now + 3)
    await ingest(source)
    assert (await projection(client, source)).map_round_winners[0].winners == [0, None, None, None, 1]

    # A misread can look map-ending too: an error read far past the verified 3-2 credits nobody.
    await ingest(video((13, 2), now + 4, status="error"))
    assert (await projection(client, source)).map_round_winners[0].winners == [0, None, None, None, 1]

    # The tracker stops reading on the map-ending score, so that score only ever arrives with the
    # error status; its round must still be recorded, or the bars show one round fewer than 13-2.
    await ingest(video((12, 2), now + 4))
    source = video((13, 2), now + 5, status="error")
    _, changed = await ingest(source)
    assert changed is True
    state = await projection(client, source)
    assert state.current_map is not None and state.current_map.scores == [13, 2]
    assert len(state.map_round_winners[0].winners) == 15 and state.map_round_winners[0].winners[-1] == 0


@pytest.mark.asyncio
async def test_multi_round_jump_keeps_trailing_unknown_rounds():
    """A leap to 4-1 credits rounds 4 and 5 to the only team that scored, since the score alone decides
    them; a leap both teams scored in keeps its rounds null, not collapsed, since their order is unknown.

    A reading more than a few rounds past VLR's confirmed 2-1 is refused by both the history
    overlay and the score raise, while VLR's 0-0 still lets a normal tracker lead through.
    """
    now = int(time.time()) - 10
    cached = detail(
        (2, 1),
        [
            Round(round_number=1, round_score="1-0", winner="team1", side="attack", win_type="Elimination"),
            Round(round_number=2, round_score="1-1", winner="team2", side="defense", win_type="Elimination"),
            Round(round_number=3, round_score="2-1", winner="team1", side="attack", win_type="Elimination"),
        ],
    )
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    async def ingest(source):
        resolved = await push.resolve_video_match(client, source)
        return await video_rounds.store_score(client, source, resolved)

    await ingest(video((2, 1), now))
    source = video((4, 1), now + 1)
    await ingest(source)

    state = await projection(client, source)
    assert state.current_map is not None
    assert state.current_map.scores == [4, 1]
    assert state.map_round_winners[0].winners == [0, 1, 0, 0, 0]

    # A rogue leap far beyond VLR's confirmed rounds never reaches the projection: both
    # apply_history and raise_map_scores refuse it, keeping VLR's score and rounds.
    rogue = video((10, 10), now + 2)
    await ingest(rogue)

    state = await projection(client, rogue)
    assert state.current_map is not None
    assert state.current_map.scores == [2, 1]
    assert state.map_round_winners[0].winners == [0, 1, 0]

    # VLR's 0-0 confirms no rounds, so the tracker's normal lead is still applied; both teams scored
    # since the last reading, so the score cannot say who won which round and they all stay unknown.
    values[constants.PUSH_DETAILS_KEY] = json.dumps({"123": detail().model_dump(mode="json")})
    values.pop(constants.VIDEO_ROUNDS_KEY.format("123", 1))
    opening = video((3, 2), now + 3)
    await ingest(opening)

    state = await projection(client, opening)
    assert state.map_round_winners[0].winners == [None] * 5
    assert state.current_map is not None
    assert state.current_map.scores == [3, 2]


@pytest.mark.asyncio
async def test_cross_team_score_split_cannot_merge_into_a_lead():
    """VLR's 10-1 with a swapped tracker 1-10 must not merge into a 10-10 projection.

    The old sum-of-both-teams check saw 11 rounds on each side and let the raise through, spending
    each team's confirmed rounds on the other; the merged per-team lead is nine rounds, not zero.
    """
    now = int(time.time()) - 10
    cached = detail((10, 1))
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    swapped = video((1, 10), now)
    resolved = await push.resolve_video_match(client, swapped)
    assert resolved is not None
    _, changed = await video_rounds.store_score(client, swapped, resolved)
    assert changed is True

    state = await projection(client, swapped)
    assert state.current_map is not None
    assert state.current_map.scores == [10, 1]


@pytest.mark.asyncio
async def test_video_raise_is_bounded_against_vlrs_unmutated_score():
    """A history overlay's allowed raise must not let the video read ride it past the bound.

    History at 5-1 is three rounds ahead of VLR's 2-1 and may stand, but the caller passes VLR's raw
    2-1 alongside it so the newer 6-1 (four merged rounds ahead) is still refused.
    """
    now = int(time.time()) - 10
    cached = detail((2, 1))
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    values[constants.VIDEO_ROUNDS_KEY.format("123", 1)] = video_rounds.VideoRounds(
        observed_at=now - 1, scores={"1": 5, "2": 1}, winners=[None] * 6
    ).model_dump_json()
    values[constants.VIDEO_SCORE_KEY] = video((6, 1), now).model_dump_json()
    client = memory_redis(values)

    state = await projection(client, video((6, 1), now))
    assert state.current_map is not None
    assert state.current_map.scores == [5, 1]


@pytest.mark.asyncio
async def test_video_raise_is_bounded_against_the_combined_history_and_video_overlay():
    """History's 2-4 and the video's 5-1 must not combine into a 5-4, six rounds past VLR's 2-1.

    The history overlay is three rounds ahead of VLR and may stand, and the video's 5-1 is three
    merged rounds ahead, but raising each team to its maximum would persist 5-4. The bound must
    judge the combined result against VLR instead of either overlay alone.
    """
    now = int(time.time()) - 10
    cached = detail((2, 1))
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    values[constants.VIDEO_ROUNDS_KEY.format("123", 1)] = video_rounds.VideoRounds(
        observed_at=now - 1, scores={"1": 2, "2": 4}, winners=[None] * 6
    ).model_dump_json()
    values[constants.VIDEO_SCORE_KEY] = video((5, 1), now).model_dump_json()
    client = memory_redis(values)

    state = await projection(client, video((5, 1), now))
    assert state.current_map is not None
    assert state.current_map.scores == [2, 4]


@pytest.mark.asyncio
async def test_vlr_rendered_winner_reconciles_wrong_tracker_inference():
    """A VLR round winner replaces a wrong tracker inference instead of staying overwritten."""
    now = int(time.time()) - 10
    cached = detail()
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    async def ingest(source):
        resolved = await push.resolve_video_match(client, source)
        return await video_rounds.store_score(client, source, resolved)

    await ingest(video((1, 0), now))
    rendered = detail(
        (0, 1),
        [Round(round_number=1, round_score="0-1", winner="team2", side="defense", win_type="Elimination")],
    )
    values[constants.PUSH_DETAILS_KEY] = json.dumps({"123": rendered.model_dump(mode="json")})

    # Without a new tracker read, the fetched VLR winner wins the projection.
    state = await projection(client, video((1, 0), now + 1))
    assert state.map_round_winners[0].winners == [1]

    # The next verified read also repairs the stored history: VLR's round 1 was team 2, so the
    # gain to 1-1 must be credited to team 1 on round 2, not to a contradicted baseline.
    source = video((1, 1), now + 2)
    await ingest(source)
    state = await projection(client, source)
    assert state.map_round_winners[0].winners == [1, 0]


@pytest.mark.asyncio
async def test_tracker_rollback_rebases_instead_of_sticking():
    """A corrected lower score replaces the stale one and drops its excess round winners."""
    now = int(time.time()) - 10
    cached = detail()
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    async def ingest(source):
        resolved = await push.resolve_video_match(client, source)
        return await video_rounds.store_score(client, source, resolved)

    await ingest(video((1, 0), now))
    await ingest(video((3, 1), now + 1))

    corrected = video((2, 1), now + 2)
    _, changed = await ingest(corrected)
    assert changed is True
    stored = await push.video_score(client)
    assert stored is not None and stored.same_score(corrected)
    assert (await projection(client, corrected)).map_round_winners[0].winners == [0, None, None]

    source = video((3, 1), now + 3)
    await ingest(source)
    assert (await projection(client, source)).map_round_winners[0].winners == [0, None, None, 0]

    # A correction that flips the won rounds clears the winner it disproves instead of leaving
    # the stored history crediting a team more rounds than its corrected score.
    flipped = video((0, 1), now + 4)
    _, changed = await ingest(flipped)
    assert changed is True
    assert (await projection(client, flipped)).map_round_winners[0].winners == [None]


@pytest.mark.asyncio
async def test_colliding_older_ingestion_cannot_overwrite_a_newer_score_or_history():
    """When requests overlap, the committed newer score and winner history stay paired."""
    now = int(time.time()) - 10
    cached = detail()
    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": cached.model_dump(mode="json")})}
    client = memory_redis(values)

    async def ingest(source):
        resolved = await push.resolve_video_match(client, source)
        return await video_rounds.store_score(client, source, resolved)

    await ingest(video((1, 0), now))
    newer = video((1, 1), now + 2)

    async def commit_newer():
        await ingest(newer)

    client.before_execute = commit_newer
    _, changed = await ingest(video((2, 0), now + 1))
    assert changed is False
    latest = await push.video_score(client)
    assert latest is not None and latest.same_score(newer)
    state = await projection(client, latest)
    assert state.current_map is not None
    assert state.current_map.scores == [1, 1]
    assert state.map_round_winners[0].winners == [0, 1]
