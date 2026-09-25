"""Select live, upcoming and recently completed matches for a client's stored favorites."""

from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, constants, schemas
from app.constants import MatchStatus
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import Favorites
from app.services import scrape_store
from app.services.subscription_store import SubscriptionStore
from app.utils import is_live


async def favorite_matches(
    session: AsyncSession, client_id: str, redis_client: Redis, include_results: bool = False
) -> list[schemas.Match]:
    """Return the next live and upcoming matches of each favorite, live first then by start time.

    Never fetches from VLR: matches come from the cron-filled match list cache, and a player counts as their
    current team from the players table, filled by the players cron (players it has not fetched yet are skipped).

    :param session: Request database session.
    :param client_id: Client UUID.
    :param redis_client: Redis client holding the match list cache.
    :param include_results: Also return completed matches that started in the last ``FAVORITE_RESULTS_WINDOW``,
        newest first, after the live and upcoming ones.
    :return: Deduplicated matches, at most ``FAVORITE_MATCHES_PER_ENTITY`` per favorite in each of the two groups.
    :raises NotFoundError: If the client is not registered.
    :raises ServiceUnavailableError: If the match list cache is empty.
    """
    favorites = await SubscriptionStore(session).get_favorites(client_id)
    if not (data := await cache.get("matches", client=redis_client)):
        # Only the cron fills the match list, so a miss never turns into per-request VLR fetches.
        raise ServiceUnavailableError("Match list is not available yet")
    listed = schemas.MatchListAdapter.validate_json(data)
    active = sorted(
        (match for match in listed if is_live(match.status) or match.status == MatchStatus.UPCOMING),
        key=lambda match: (not is_live(match.status), match.time),
    )
    player_teams = await scrape_store.player_team_ids(session, favorites.players)
    team_ids = [*favorites.teams, *(team_id for team_id in player_teams.values() if team_id)]
    selected = _select(active, favorites, team_ids)
    if include_results:
        since = datetime.now(UTC) - constants.FAVORITE_RESULTS_WINDOW
        results = sorted(
            (match for match in listed if match.status == MatchStatus.COMPLETED and match.time >= since),
            key=lambda match: match.time,
            reverse=True,
        )
        selected += _select(results, favorites, team_ids)
    return selected


def _select(pool: list[schemas.Match], favorites: Favorites, team_ids: list[str]) -> list[schemas.Match]:
    """Keep the first matches of each favorite from an ordered pool.

    :param pool: Matches in display order.
    :param favorites: The client's favorites.
    :param team_ids: Favorited teams plus the current teams of favorited players.
    :return: Matches of the pool, in pool order, at most ``FAVORITE_MATCHES_PER_ENTITY`` per favorite.
    """
    groups = [
        *([match for match in pool if team_id in (match.team1.id, match.team2.id)] for team_id in team_ids),
        *([match for match in pool if match.event_id == event_id] for event_id in favorites.events),
        *([match for match in pool if match.id == match_id] for match_id in favorites.matches),
    ]
    selected = {match.id for group in groups for match in group[: constants.FAVORITE_MATCHES_PER_ENTITY]}
    return [match for match in pool if match.id in selected]
