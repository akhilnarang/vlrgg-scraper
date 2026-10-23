"""Write-once match insights: a model prediction and summaries generated when a match is opened.

Before a match, within ``INSIGHTS_PRE_MATCH_WINDOW`` of its start, the first request
generates the prediction and the pre-match summary; after the final, the post-match
summary, for a match that started within ``INSIGHTS_POST_MATCH_WINDOW``. Each is stored
once and never regenerated. One request per match generates, holding a Redis claim,
while the others serve what is already stored; the generating request waits at most
until its deadline and the generation finishes in the background, within its own time
limit. A failed attempt stores nothing and is retried only after a cooldown.
"""

import asyncio
import logging
import math
import secrets
import time
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cache, utils
from app.constants import (
    INSIGHTS_CLAIM_KEY,
    INSIGHTS_CLAIM_MARGIN_SECONDS,
    INSIGHTS_CLEANUP_SECONDS,
    INSIGHTS_COOLDOWN_KEY,
    INSIGHTS_COOLDOWN_SECONDS,
    INSIGHTS_GENERATION_OVERHEAD_SECONDS,
    INSIGHTS_HISTORY_DAYS,
    INSIGHTS_NO_DATA_COOLDOWN_SECONDS,
    INSIGHTS_POST_MATCH_WINDOW,
    INSIGHTS_PRE_MATCH_WINDOW,
    INSIGHTS_READ_RESERVE_SECONDS,
    INSIGHTS_SHUTDOWN_SECONDS,
    MATCH_TIMEZONE,
    PREDICTION_TIMEOUT_SECONDS,
    TBD,
)
from app.core import connections
from app.core.config import settings
from app.db.models import MatchInsightRecord
from app.enums import InsightKind, MatchStatus, PredictionFallbackReason
from app.schemas.matches import InsightPrediction, MatchDetailsResponse, MatchInsights, MatchSummary, MatchWithDetails
from app.services import history, insight_store, predictions, ranking_store, summaries
from app.services.summary_prompts import POST_MATCH_PROMPT, PRE_MATCH_PROMPT, PROMPT_VERSION

logger = logging.getLogger(__name__)

_MATCH_TZ = ZoneInfo(MATCH_TIMEZONE)
# Deletes the claim only while it still holds this generation's token, so a late finish cannot free another's.
_RELEASE_CLAIM = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_STORES = {
    InsightKind.PREDICTION: insight_store.store_prediction,
    InsightKind.PRE_SUMMARY: insight_store.store_pre_summary,
    InsightKind.POST_SUMMARY: insight_store.store_post_summary,
}
# Generations that outlive their request, referenced so the event loop does not drop them.
_generations: set[asyncio.Task] = set()

Sessions = async_sessionmaker[AsyncSession]


class _NothingToSay(Exception):
    """An insight has no useful data to be written from yet."""


async def attach(match_id: str, details: MatchWithDetails, deadline: float) -> MatchDetailsResponse:
    """Add a match's insights to its details, generating due ones when Redis can coordinate it.

    Every database and Redis call made for the request runs within its deadline; once it has
    passed, the details are returned without insights. A timeout, an unavailable database or
    Redis, or any insight failure leaves the insights read by then, if any.

    :param match_id: Match ID.
    :param details: Match details as cached or scraped.
    :param deadline: ``time.monotonic()`` value after which the request stops working on insights.
    :return: The details with the insights stored for their current teams, or none.
    """
    if not settings.PREDICTION_SERVICE_URL and not settings.match_summaries_enabled:
        return MatchDetailsResponse(**dict(details), insights=None)
    record = None
    sessions = connections.subscription_sessions
    try:
        if sessions is not None and (remaining := deadline - time.monotonic()) > 0:
            async with asyncio.timeout(remaining):
                record = await _read(sessions, match_id)
                due = _due(record, details, datetime.now(UTC))
                if (
                    due
                    and connections.redis_pool is not None
                    and await _generate_due(sessions, match_id, details, due, deadline)
                ):
                    record = await _read(sessions, match_id)
    except TimeoutError:
        logger.info("ran out of time for the insights of match %s; serving those read so far", match_id)
    except Exception:
        logger.warning("could not attach insights to match %s", match_id, exc_info=True)
    return MatchDetailsResponse(**dict(details), insights=_public(record, details))


async def _read(sessions: Sessions, match_id: str) -> MatchInsightRecord | None:
    """Read a match's stored insights in a short session of its own.

    :param sessions: Session factory.
    :param match_id: Match ID.
    :return: The stored row, or None.
    """
    async with sessions() as session:
        return await insight_store.get(session, match_id)


def _team_ids(details: MatchWithDetails) -> tuple[str, str] | None:
    """Identify a match's two teams.

    :param details: Match details.
    :return: Both team IDs in header order, or None unless two distinct known teams are set.
    """
    if len(details.teams) != 2:
        return None
    team_a, team_b = details.teams
    if not team_a.id or not team_b.id or team_a.id == team_b.id:
        return None
    if TBD in {team_a.name.strip().casefold(), team_b.name.strip().casefold()}:
        return None
    return team_a.id, team_b.id


def _pre_match_teams(details: MatchWithDetails, now: datetime) -> tuple[str, str] | None:
    """Identify the teams of an upcoming match that starts within the pre-match window.

    :param details: Match details.
    :param now: Current time.
    :return: Both team IDs, or None when pre-match insights are not due.
    """
    start = details.event.date
    if (details.event.status or "").strip().casefold() != MatchStatus.UPCOMING or start is None:
        return None
    if not start - INSIGHTS_PRE_MATCH_WINDOW <= now < start:
        return None
    return _team_ids(details)


def _due(record: MatchInsightRecord | None, details: MatchWithDetails, now: datetime) -> set[InsightKind]:
    """List the enabled insights a match lacks and is eligible for now.

    :param record: Stored insights.
    :param details: Match details.
    :param now: Current time.
    :return: Kinds to generate.
    """
    kinds = set()
    if _pre_match_teams(details, now) is not None:
        if settings.PREDICTION_SERVICE_URL:
            kinds.add(InsightKind.PREDICTION)
        if settings.match_summaries_enabled:
            kinds.add(InsightKind.PRE_SUMMARY)
    start = details.event.date
    if (
        settings.match_summaries_enabled
        and utils.is_final(details.event.status)
        and start is not None
        and now - start <= INSIGHTS_POST_MATCH_WINDOW
        and _team_ids(details) is not None
    ):
        kinds.add(InsightKind.POST_SUMMARY)
    return {kind for kind in kinds if record is None or getattr(record, kind) is None}


async def _not_cooling(client: Redis, match_id: str, kinds: set[InsightKind]) -> set[InsightKind]:
    """Drop kinds whose last attempt failed recently.

    :param client: Redis client.
    :param match_id: Match ID.
    :param kinds: Kinds to check.
    :return: Kinds that may be attempted.
    """
    return {kind for kind in kinds if not await client.exists(INSIGHTS_COOLDOWN_KEY.format(kind, match_id))}


async def _generate_due(
    sessions: Sessions, match_id: str, details: MatchWithDetails, due: set[InsightKind], deadline: float
) -> bool:
    """Generate a match's due insights unless another request already is.

    Waiting on the generation stops ``INSIGHTS_READ_RESERVE_SECONDS`` before the deadline,
    leaving time within it to read what was stored meanwhile.

    :param sessions: Session factory.
    :param match_id: Match ID.
    :param details: Match details.
    :param due: Kinds the match lacks.
    :param deadline: ``time.monotonic()`` value after which the request stops working on insights.
    :return: True when this request's generation may have stored insights.
    """
    client = cache.get_client()
    try:
        if not await _not_cooling(client, match_id, due):
            return False
        with suppress(TimeoutError):
            async with asyncio.timeout(deadline - INSIGHTS_READ_RESERVE_SECONDS - time.monotonic()):
                task = asyncio.create_task(_generate(sessions, match_id, details))
                _generations.add(task)
                task.add_done_callback(partial(_finished, match_id))
                # Shielded, so a slow generation, or a client that disconnects, leaves it running to store its result.
                return await asyncio.shield(task)
        return True
    except RedisError:
        logger.warning("could not coordinate insights for match %s; serving stored ones", match_id, exc_info=True)
        return False
    finally:
        await client.aclose()


def _finished(match_id: str, task: asyncio.Task) -> None:
    """Forget a finished generation and log a failure it did not handle.

    :param match_id: Match ID.
    :param task: Finished generation.
    """
    _generations.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("the insight generation of match %s failed", match_id, exc_info=error)


async def stop_generations() -> None:
    """Let running generations finish within ``INSIGHTS_SHUTDOWN_SECONDS``, then cancel the rest.

    Call before closing the database and Redis they use.
    """
    if not _generations:
        return
    _, pending = await asyncio.wait(set(_generations), timeout=INSIGHTS_SHUTDOWN_SECONDS)
    for task in pending:
        task.cancel()
    # A cancelled generation still cools down and releases its claim, each within INSIGHTS_CLEANUP_SECONDS.
    await asyncio.gather(*pending, return_exceptions=True)


async def _generate(sessions: Sessions, match_id: str, details: MatchWithDetails) -> bool:
    """Claim a match's generation, generate and store the insights it still lacks, then release the claim.

    The claim is taken here rather than by the request, so a request that runs out of time
    while claiming cannot leave a claim behind with no generation holding it. A generation
    runs a prediction and one summary, since a match is never both upcoming and final, so
    it is cut off, claim included, after their timeouts plus an overhead, and its claim
    outlasts that. The release runs within a short limit of its own.

    :param sessions: Session factory.
    :param match_id: Match ID.
    :param details: Match details.
    :return: False when another generation holds the claim.
    """
    limit = PREDICTION_TIMEOUT_SECONDS + settings.SUMMARY_TIMEOUT_SECONDS + INSIGHTS_GENERATION_OVERHEAD_SECONDS
    key = INSIGHTS_CLAIM_KEY.format(match_id)
    token = secrets.token_hex(16)
    client = cache.get_client()
    try:
        async with asyncio.timeout(limit):
            if not await client.set(key, token, nx=True, ex=math.ceil(limit + INSIGHTS_CLAIM_MARGIN_SECONDS)):
                return False
            record = await _read(sessions, match_id)
            due = await _not_cooling(client, match_id, _due(record, details, datetime.now(UTC)))
            prediction = _for_teams(record.prediction if record else None, _team_ids(details))
            # Each producer checks eligibility again right before its provider call too.
            if InsightKind.PREDICTION in due and (teams := _pre_match_teams(details, datetime.now(UTC))):
                prediction = await _attempt(
                    sessions, client, match_id, InsightKind.PREDICTION, _predict(sessions, details, teams)
                )
            if InsightKind.PRE_SUMMARY in due and (teams := _pre_match_teams(details, datetime.now(UTC))):
                await _attempt(
                    sessions,
                    client,
                    match_id,
                    InsightKind.PRE_SUMMARY,
                    _pre_summary(sessions, match_id, details, teams, prediction),
                )
            if InsightKind.POST_SUMMARY in due:
                await _attempt(sessions, client, match_id, InsightKind.POST_SUMMARY, _post_summary(details))
    except Exception:
        logger.warning("could not generate insights for match %s", match_id, exc_info=True)
    finally:
        # Also after a claim that timed out, which Redis may have applied; another's claim keeps its token.
        try:
            async with asyncio.timeout(INSIGHTS_CLEANUP_SECONDS):
                await client.register_script(_RELEASE_CLAIM)(keys=[key], args=[token])
        except RedisError, TimeoutError:
            logger.warning("could not release the insights claim of match %s", match_id, exc_info=True)
        finally:
            await client.aclose()
    return True


async def _attempt(
    sessions: Sessions, client: Redis, match_id: str, kind: InsightKind, produce: Awaitable[dict | None]
) -> dict | None:
    """Produce one insight and store it, or start its cooldown.

    :param sessions: Session factory.
    :param client: Redis client.
    :param match_id: Match ID.
    :param kind: Insight kind.
    :param produce: Producer of the payload; None means it failed, and ``_NothingToSay`` that it lacks data.
    :return: The stored payload, or None after a failure.
    """
    cooldown = INSIGHTS_COOLDOWN_SECONDS
    try:
        if (payload := await produce) is not None:
            async with sessions.begin() as session:
                await _STORES[kind](session, match_id, payload, datetime.now(UTC))
            return payload
        logger.info("no %s for match %s; retrying after the cooldown", kind, match_id)
    except _NothingToSay:
        logger.info("nothing useful for the %s of match %s yet; retrying after the cooldown", kind, match_id)
        cooldown = INSIGHTS_NO_DATA_COOLDOWN_SECONDS
    except asyncio.CancelledError:
        # The generation's time limit, or shutdown, cut the attempt off; it failed all the same.
        await _cool_down(client, match_id, kind, cooldown)
        raise
    except Exception:
        logger.warning("could not generate the %s of match %s", kind, match_id, exc_info=True)
    await _cool_down(client, match_id, kind, cooldown)
    return None


async def _cool_down(client: Redis, match_id: str, kind: InsightKind, seconds: int) -> None:
    """Hold off further attempts at one insight after a failed one.

    :param client: Redis client.
    :param match_id: Match ID.
    :param kind: Insight kind.
    :param seconds: How long to hold off.
    """
    try:
        async with asyncio.timeout(INSIGHTS_CLEANUP_SECONDS):
            await client.set(INSIGHTS_COOLDOWN_KEY.format(kind, match_id), 1, ex=seconds)
    except RedisError, TimeoutError:
        logger.warning("could not start the %s cooldown of match %s", kind, match_id, exc_info=True)


async def _predict(sessions: Sessions, details: MatchWithDetails, teams: tuple[str, str]) -> dict | None:
    """Ask the prediction service for the series.

    VLR rarely shows the patch of an upcoming match, so the newest stored match's patch stands in,
    as ``/api/v2/rankings/predict`` does.

    :param sessions: Session factory.
    :param details: Upcoming match.
    :param teams: Both team IDs.
    :return: Model probabilities with their team IDs, or None when the service gave none or the match has started.
    """
    start = details.event.date
    if start is None:
        return None
    patch = details.event.patch
    if not predictions.is_model_patch(patch):
        async with sessions() as session:
            patch = await ranking_store.latest_patch(session)
    if _pre_match_teams(details, datetime.now(UTC)) != teams:
        return None
    result = await predictions.predict_match(*teams, as_of=start.astimezone(UTC).date(), patch=patch)
    if isinstance(result, PredictionFallbackReason):
        return None
    return {"team_a_id": teams[0], "team_b_id": teams[1], **result.model_dump(mode="json")}


async def _pre_summary(
    sessions: Sessions, match_id: str, details: MatchWithDetails, teams: tuple[str, str], prediction: dict | None
) -> dict | None:
    """Write the pre-match summary from the teams' recent head-to-head series and the prediction.

    The ledger dates matches by their America/New_York day, so the history runs
    ``INSIGHTS_HISTORY_DAYS`` days through the day before the match's day there.
    A match earlier on the same day is left out, which only approximates "before
    the start".

    :param sessions: Session factory.
    :param match_id: Match ID, left out of the history.
    :param details: Upcoming match.
    :param teams: Both team IDs.
    :param prediction: Stored or new prediction for the teams.
    :return: The summary payload, or None without a valid answer or once the match has started.
    :raises _NothingToSay: Without a head-to-head record or a prediction.
    """
    start = details.event.date
    if start is None:
        return None
    through = start.astimezone(_MATCH_TZ).date() - timedelta(days=1)
    window = history.HistoryFilter(
        from_date=through - timedelta(days=INSIGHTS_HISTORY_DAYS - 1), through_date=through, exclude_match_id=match_id
    )
    async with sessions() as session:
        head_to_head = await history.head_to_head(session, *teams, window)
    if (data := summaries.pre_match_input(details, prediction, head_to_head)) is None:
        raise _NothingToSay
    # The history queries may have run past the start.
    if _pre_match_teams(details, datetime.now(UTC)) != teams:
        return None
    text = await summaries.generate(PRE_MATCH_PROMPT, data)
    if text is None:
        return None
    return {
        "text": text,
        "model": settings.LLM_MODEL,
        "prompt_version": PROMPT_VERSION,
        "team_a_id": teams[0],
        "team_b_id": teams[1],
        "start": start.isoformat(),
    }


async def _post_summary(details: MatchWithDetails) -> dict | None:
    """Write the post-match summary from the series' maps, rounds, and players.

    :param details: Final match.
    :return: The summary payload, or None when the answer is invalid.
    :raises _NothingToSay: When VLR does not show the whole series.
    """
    if (data := summaries.post_match_input(details)) is None:
        raise _NothingToSay
    text = await summaries.generate(POST_MATCH_PROMPT, data)
    if text is None:
        return None
    team_a, team_b = details.teams
    return {
        "text": text,
        "model": settings.LLM_MODEL,
        "prompt_version": PROMPT_VERSION,
        "team_a_id": team_a.id,
        "team_b_id": team_b.id,
    }


def _for_teams(payload: dict | None, teams: tuple[str, str] | None) -> dict | None:
    """Keep a stored payload only if it was made for the match's current teams.

    :param payload: Stored payload with ``team_a_id`` and ``team_b_id``.
    :param teams: Current team IDs.
    :return: The payload, or None when either side has changed since.
    """
    if payload is None or teams is None or {payload.get("team_a_id"), payload.get("team_b_id")} != set(teams):
        return None
    return payload


def _stored[T: BaseModel](
    model: type[T], payload: dict | None, generated_at: datetime | None, teams: tuple[str, str] | None
) -> T | None:
    """Expose one stored artifact if it describes the match's current teams.

    :param model: Public model of the artifact.
    :param payload: Stored payload.
    :param generated_at: Time the artifact was stored.
    :param teams: Current team IDs.
    :return: The artifact, or None when absent, made for other teams, or malformed.
    """
    if (payload := _for_teams(payload, teams)) is None:
        return None
    try:
        return model.model_validate({**payload, "generated_at": generated_at})
    except ValidationError:
        logger.warning("a stored %s is malformed", model.__name__, exc_info=True)
        return None


def _public(record: MatchInsightRecord | None, details: MatchWithDetails) -> MatchInsights | None:
    """Expose the stored insights that describe the match's current teams.

    :param record: Stored insights.
    :param details: Match details.
    :return: Insights, or None when none apply.
    """
    if record is None:
        return None
    teams = _team_ids(details)
    insights = MatchInsights(
        prediction=_stored(InsightPrediction, record.prediction, record.prediction_at, teams),
        pre_match_summary=_stored(MatchSummary, record.pre_summary, record.pre_summary_at, teams),
        post_match_summary=_stored(MatchSummary, record.post_summary, record.post_summary_at, teams),
    )
    return insights if insights != MatchInsights() else None
