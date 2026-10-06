"""Extend the Elo ledger with matches that left the live listing as completed."""

import logging
import time

from sentry_sdk import get_current_scope

from app import schemas, utils
from app.core import connections
from app.services import matches, ranking_store, team_rankings
from app.services.events import tier_event_circuits

logger = logging.getLogger(__name__)

# An event still assigned no circuit is re-checked against the tier listings after this long.
OTHER_RECHECK_SECONDS = 86400


async def _recheck_other_events(sessions, now: int) -> bool:
    """Look events assigned no circuit up in the tier listings again.

    An event VLR had not listed on a tier tab when it was ingested may be listed
    now. The check time is refreshed either way, so an event VLR never lists is
    asked about once a day rather than on every run.

    :param sessions: Session factory for the ranking database.
    :param now: Unix time of this run.
    :return: True when at least one event's circuit changed.
    """
    async with sessions() as session:
        stale = await ranking_store.stale_other_events(session, now - OTHER_RECHECK_SECONDS)
    if not stale:
        return False
    # Tier listings are crawled by the service, bounded to their first pages.
    circuits = await tier_event_circuits(stop_ids={event.id for event in stale})
    changed = False
    async with sessions.begin() as session:
        for event in stale:
            circuit = circuits.get(event.id, event.circuit)
            changed |= circuit != event.circuit
            await ranking_store.set_event_circuit(session, event.id, event.name, circuit, now)
    return changed


async def team_rankings_cron(ctx: dict) -> None:
    """Upsert completed matches missing, incomplete, or contradicted in the ledger.

    Each match is upserted in its own transaction, so one fetch failure cannot
    block the others, and the ratings are rebuilt from the stored matches once
    at the end. The rebuild is deterministic, so the result never depends on
    how many matches a run fetched or in what order they arrived.

    A run that changed nothing still rebuilds when the ledger is stale, which
    recovers a run that committed its upserts but died before its rebuild.
    Events that got no tier listing are re-checked once their check has aged out,
    because VLR may list them after the fact.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Team Rankings Cron")
    sessions = connections.subscription_sessions
    if sessions is None:
        return
    now = int(time.time())
    changed = False
    raw = await ctx["redis"].get("matches")
    if raw:
        completed = [match for match in schemas.MatchListAdapter.validate_json(raw) if utils.is_final(match.status)]
        if completed:
            async with sessions() as session:
                stored = await ranking_store.stored_listings(session)
            pending = sorted(
                (match for match in completed if team_rankings.needs_repair(match, stored.get(match.id), now)),
                key=lambda match: (match.time, match.id),
            )
            for listed in pending:
                try:
                    details = await matches.match_by_id(listed.id, ctx["redis"])
                except Exception:
                    logger.warning("could not fetch match %s for rankings; will retry", listed.id, exc_info=True)
                    continue
                try:
                    async with sessions.begin() as session:
                        circuit = await team_rankings.resolve_event_circuit(
                            session, details.event.id, details.event.series
                        )
                        changed |= await team_rankings.ingest_match(session, listed.id, details, circuit)
                except Exception:
                    logger.warning("could not ingest match %s; will retry", listed.id, exc_info=True)
    try:
        changed |= await _recheck_other_events(sessions, now)
    except Exception:
        logger.warning("could not re-check events with no circuit; will retry", exc_info=True)
    rebuild = changed
    if not rebuild:
        async with sessions() as session:
            rebuild = await ranking_store.needs_rebuild(session)
    if not rebuild:
        return
    async with sessions.begin() as session:
        await team_rankings.rebuild_ratings(session)
