"""Link games from Riot's esports match API to the VLR maps they were, and store them."""

import logging
import time
from collections import defaultdict
from http import HTTPStatus

import httpx2
from sentry_sdk import get_current_scope
from sqlalchemy import select

from app import schemas
from app.constants import RIOT_RUN_BUDGET
from app.core import connections
from app.db.models import RiotMatch
from app.schemas.riot import Candidate, Game, GameLink
from app.services import riot

logger = logging.getLogger(__name__)


async def riot_matches_cron(ctx: dict) -> None:
    """Store each game Riot lists that links to exactly one VLR map for sure.

    Riot lists a game for about 12 hours, so a game with no fitting VLR map yet, such as one VLR has not scored,
    is tried again on every run. A game the model turned down, or that competes for a map, is remembered and not
    asked about again. Only linked games are stored, and a VLR map that several listed games link to, such as a
    voided game and its replay, gets none of them. Checking stops when the run's time budget is spent or Riot
    rate-limits it, and what was found so far is still stored.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Riot Matches Cron")
    sessions = connections.subscription_sessions
    redis = ctx["redis"]
    raw = await redis.get("matches")
    if sessions is None or not raw:
        return
    deadline = time.monotonic() + RIOT_RUN_BUDGET.total_seconds()
    listing = schemas.MatchListAdapter.validate_json(raw)
    pages: dict[str, schemas.MatchWithDetails] = {}
    claims: dict[tuple[str, int], list[tuple[Game, Candidate, GameLink]]] = defaultdict(list)
    async with riot.client() as client:
        try:
            game_ids = await riot.recent_game_ids(client)
        except httpx2.HTTPError as error:
            logger.warning("could not list Riot's recent games: %s", error)
            return
        async with sessions() as session:
            stored = set(await session.scalars(select(RiotMatch.riot_id).where(RiotMatch.riot_id.in_(game_ids))))
        pending = [riot_id for riot_id in game_ids if riot_id not in stored]
        skipped = await riot.rejected(redis, pending)
        for riot_id in pending:
            if riot_id in skipped:
                continue
            if time.monotonic() > deadline:
                logger.info("Riot run out of time; later games wait for the next run")
                break
            try:
                game = riot.parse_game(riot_id, await riot.game(client, riot_id))
                if game is None:
                    await riot.reject(redis, riot_id)
                    continue
                found = await riot.find_link(game, listing, redis, pages)
            except httpx2.HTTPStatusError as error:
                if error.response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                    logger.warning("Riot rate-limited the run; stopping until the next one")
                    break
                logger.warning("could not fetch Riot game %s; will retry", riot_id, exc_info=True)
                continue
            except Exception:
                logger.warning("could not link Riot game %s; will retry", riot_id, exc_info=True)
                continue
            if found is not None:
                candidate, link = found
                claims[(candidate.match_id, candidate.map_number)].append((game, candidate, link))

    for (match_id, map_number), games in claims.items():
        if len(games) > 1:
            riot_ids = [game.riot_id for game, _, _ in games]
            logger.warning("Riot games %s all link to VLR %s map %s; storing none", riot_ids, match_id, map_number)
            for riot_id in riot_ids:
                await riot.reject(redis, riot_id)
            continue
        [(game, candidate, link)] = games
        try:
            async with sessions.begin() as session:
                linked = await riot.store(session, game, candidate, link)
        except Exception:
            logger.warning("could not store Riot game %s; will retry", game.riot_id, exc_info=True)
            continue
        if linked:
            logger.info("linked Riot game %s to VLR %s map %s", game.riot_id, match_id, map_number)
        else:
            logger.warning(
                "Riot game %s or VLR %s map %s is already stored; not storing it", game.riot_id, match_id, map_number
            )
            await riot.reject(redis, game.riot_id)
