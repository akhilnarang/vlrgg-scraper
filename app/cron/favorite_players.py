"""Fetch favorited players in small batches, so favorite-match lookups know each player's current team."""

import asyncio
import logging
import time

from sentry_sdk import get_current_scope

from app import constants
from app.core import connections
from app.services import player, scrape_store
from app.services.subscription_store import SubscriptionStore

logger = logging.getLogger(__name__)


async def favorite_players_cron(ctx: dict) -> None:
    """Fetch unknown favorited players first, then stale ones, one at a time and at most a batch per run.

    :param ctx: arq job context.
    :return: None.
    """
    get_current_scope().set_transaction_name("Favorite Players Cron")
    sessions = connections.subscription_sessions
    if sessions is None:
        return
    async with sessions() as session:
        player_ids = await SubscriptionStore(session).favorited_player_ids()
        fetched_at = await scrape_store.player_fetch_times(session, player_ids)

    stale_before = time.time() - constants.FAVORITE_PLAYER_MAX_AGE
    due = sorted(
        (player_id for player_id in player_ids if fetched_at.get(player_id, 0) < stale_before),
        key=lambda player_id: fetched_at.get(player_id, 0),
    )[: constants.FAVORITE_PLAYER_REFRESH_BATCH]
    stored = 0
    for index, player_id in enumerate(due):
        if index:
            await asyncio.sleep(constants.FAVORITE_PLAYER_REFRESH_DELAY)
        try:
            data = await player.get_player_data(player_id)
            # One short transaction per player, opened only after the fetch.
            async with sessions.begin() as session:
                await scrape_store.upsert_player(session, player_id, data)
        except Exception:
            logger.warning("could not fetch favorited player %s", player_id, exc_info=True)
            continue
        stored += 1
    logger.info("fetched %d of %d due favorited players (%d favorited)", stored, len(due), len(player_ids))
