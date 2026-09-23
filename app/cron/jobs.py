import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from firebase_admin import messaging
from sentry_sdk import capture_exception, get_current_scope

from app import constants, schemas
from app.constants import MatchStatus
from app.core.config import settings
from app.services import events, matches, news, rankings, standings
from app.services.fcm import get_app

logger = logging.getLogger(__name__)


async def fcm_notification_cron(ctx: dict) -> None:
    """
    Function to notify users about upcoming matches
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("FCM Notification Cron")
    client = ctx["redis"]

    # Get the current time, so that we can filter for matches starting in the next 15 minutes
    current_time = datetime.now(tz=ZoneInfo(settings.TIMEZONE))
    upcoming_matches = [
        match
        for match in await matches.get_upcoming_matches(redis_client=client)
        if match.status == MatchStatus.UPCOMING and 0 < (match.time - current_time).total_seconds() < 900
    ]

    if not upcoming_matches:
        logger.info("No notifications to send")
        return

    # Fetch all match details concurrently (skip failures gracefully)
    all_match_details = await asyncio.gather(
        *[matches.match_by_id(match.id, redis_client=client) for match in upcoming_matches],
        return_exceptions=True,
    )

    # Build notification messages, skipping any failed lookups
    messages = []
    for match, match_details in zip(upcoming_matches, all_match_details):
        if isinstance(match_details, BaseException):
            capture_exception(match_details)
            logger.warning(f"Failed to fetch match details for {match.id}: {match_details}")
            continue

        logger.info(f"Sending notification for {match=}")

        team1_id, team2_id = (team.id for team in match_details.teams)
        time_to_start = int((match.time - current_time).total_seconds() // 60)

        payload = {
            "title": f"{match.team1.name} vs {match.team2.name}",
            "body": f"Match is starting in {time_to_start} minutes",
            "timestamp": match.time.isoformat(),
            "match_id": match.id,
        }
        if streams := match_details.videos.streams:
            payload |= {"stream_url": streams[0].url.unicode_string()}

        messages.append(
            messaging.Message(
                data=payload,
                condition=f"'event-{match_details.event.id}' in topics || 'match-{match.id}' in topics || "
                f"'team-{team1_id}' in topics || 'team-{team2_id}' in topics",
                android=messaging.AndroidConfig(ttl=timedelta(minutes=30)),
            ),
        )

    # Don't bother sending if there's nothing to send
    if not messages:
        return

    await messaging.send_each_async(messages=messages, dry_run=False, app=get_app())
    logger.info("Sent notification")


async def rankings_cron(ctx: dict) -> None:
    """
    Function to fetch rankings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Rankings Cron")

    await ctx["redis"].set(
        "rankings",
        schemas.RankingListAdapter.dump_json(await rankings.ranking_list()),
        ex=constants.CACHE_TTL_RANKINGS,
    )


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
