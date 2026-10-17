"""Link games from Riot's esports match API to the VLR maps they were, and store them."""

import logging
from collections import defaultdict

from sentry_sdk import get_current_scope
from sqlalchemy import select

from app import schemas
from app.core import connections
from app.db.models import RiotMatch
from app.schemas.riot import Candidate, Game, GameLink
from app.services import riot

logger = logging.getLogger(__name__)


async def riot_matches_cron(ctx: dict) -> None:
    """Store each game Riot lists that links to exactly one VLR map for sure.

    Riot lists a game for about 12 hours, so a game not linked yet, such as one VLR has not scored, is tried
    again on every run until it links or leaves the list. Only linked games are stored, and a VLR map that
    several listed games link to, such as a voided game and its replay, gets none of them.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Riot Matches Cron")
    sessions = connections.subscription_sessions
    raw = await ctx["redis"].get("matches")
    if sessions is None or not raw:
        return
    listing = schemas.MatchListAdapter.validate_json(raw)
    claims: dict[tuple[str, int], list[tuple[Game, Candidate, GameLink]]] = defaultdict(list)
    async with riot.client() as client:
        game_ids = await riot.recent_game_ids(client)
        async with sessions() as session:
            stored = set(await session.scalars(select(RiotMatch.riot_id).where(RiotMatch.riot_id.in_(game_ids))))
        for riot_id in game_ids:
            if riot_id in stored:
                continue
            try:
                game = riot.parse_game(riot_id, await riot.game(client, riot_id))
                if game is None:
                    continue
                found = await riot.find_link(game, listing, ctx["redis"])
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
                "VLR %s map %s is already linked to another Riot game; not storing %s",
                match_id,
                map_number,
                game.riot_id,
            )
