"""Extend the Elo ledger with matches that left the live listing as completed."""

import logging

from sentry_sdk import get_current_scope

from app import schemas, utils
from app.core import connections
from app.services import matches, ranking_store, team_rankings

logger = logging.getLogger(__name__)


async def team_rankings_cron(ctx: dict) -> None:
    """Upsert completed matches missing, incomplete, or contradicted in the ledger.

    Each match is upserted in its own transaction, so one fetch failure cannot
    block the others, and the ratings are rebuilt from the stored matches once
    at the end. The rebuild is deterministic, so the result never depends on
    how many matches a run fetched or in what order they arrived.

    A run that changed nothing still rebuilds when the ledger is stale, which
    recovers a run that committed its upserts but died before its rebuild.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Team Rankings Cron")
    sessions = connections.subscription_sessions
    if sessions is None:
        return
    raw = await ctx["redis"].get("matches")
    if not raw:
        return
    completed = [match for match in schemas.MatchListAdapter.validate_json(raw) if utils.is_final(match.status)]
    if not completed:
        return
    async with sessions() as session:
        stored = await ranking_store.stored_listings(session)
    pending = sorted(
        (match for match in completed if team_rankings.needs_repair(match, stored.get(match.id))),
        key=lambda match: (match.time, match.id),
    )
    changed = False
    for listed in pending:
        try:
            details = await matches.match_by_id(listed.id, ctx["redis"])
        except Exception:
            logger.warning("could not fetch match %s for rankings; will retry", listed.id, exc_info=True)
            continue
        try:
            async with sessions.begin() as session:
                circuit = await team_rankings.resolve_event_circuit(session, details.event.id, details.event.series)
                changed |= await team_rankings.ingest_match(session, listed.id, details, circuit)
        except Exception:
            logger.warning("could not ingest match %s; will retry", listed.id, exc_info=True)
    rebuild = changed
    if not rebuild:
        async with sessions() as session:
            rebuild = await ranking_store.needs_rebuild(session)
    if not rebuild:
        return
    async with sessions.begin() as session:
        await team_rankings.rebuild_ratings(session)
