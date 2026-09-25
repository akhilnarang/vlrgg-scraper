import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sentry_sdk import get_current_scope
from sqlalchemy.exc import SQLAlchemyError

from app import constants, schemas
from app.core import connections
from app.core.config import settings
from app.services import events, matches, news, rankings, scrape_store, standings

logger = logging.getLogger(__name__)


async def rankings_cron(ctx: dict) -> None:
    """
    Function to fetch rankings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Rankings Cron")

    ranking_list = await rankings.ranking_list()
    await ctx["redis"].set(
        "rankings", schemas.RankingListAdapter.dump_json(ranking_list), ex=constants.CACHE_TTL_RANKINGS
    )
    if (sessions := connections.subscription_sessions) is None:
        return
    try:
        async with sessions.begin() as session:
            for region in ranking_list:
                for team in region.teams:
                    await scrape_store.upsert_team(
                        session,
                        str(team.id),
                        name=team.name,
                        logo=str(team.logo),
                        rank=team.rank,
                        country=team.country,
                        region=region.region,
                    )
    except SQLAlchemyError:
        # The store never fails the cron; the rankings cache is its job.
        logger.warning("could not store ranked teams", exc_info=True)


async def matches_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Matches Cron")
    client = ctx["redis"]

    await client.set(
        "matches",
        schemas.MatchListAdapter.dump_json(await matches.match_list(redis_client=client)),
        ex=constants.CACHE_TTL_MATCHES,
    )


async def events_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Events Cron")
    client = ctx["redis"]

    await client.set(
        "events",
        schemas.EventListAdapter.dump_json(await events.get_events(cache_client=client)),
        ex=constants.CACHE_TTL_EVENTS,
    )


async def news_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("News Cron")

    await ctx["redis"].set(
        "news",
        schemas.NewsListAdapter.dump_json(await news.news_list()),
        ex=constants.CACHE_TTL_NEWS,
    )


async def standings_cron(ctx: dict) -> None:
    """
    Function to fetch standings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Standings Cron")
    client = ctx["redis"]
    current_year = datetime.now(ZoneInfo(settings.TIMEZONE)).year

    result = await standings.standings_list(current_year)
    await client.set(
        f"standings_{current_year}",
        result.model_dump_json(),
        ex=constants.CACHE_TTL_STANDINGS,
    )
