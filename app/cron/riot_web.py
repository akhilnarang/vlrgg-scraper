"""The Riot web crons: run the schedule, details and bracket refreshes and log their outcome."""

import logging

from sentry_sdk import get_current_scope

from app.core import connections
from app.core.config import settings
from app.services import brackets, riot_web

logger = logging.getLogger(__name__)


async def riot_web_schedule_cron(ctx: dict) -> None:
    """Store Riot's schedule and link the series it shows to their VLR matches.

    Runs only while ``RIOT_WEB_API_KEY`` is set.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Riot Web Schedule Cron")
    sessions = connections.subscription_sessions
    if not settings.RIOT_WEB_API_KEY or sessions is None:
        return
    if summary := await riot_web.refresh_schedule(sessions, ctx["redis"]):
        logger.info("riot schedule: %s", summary)


async def riot_web_details_cron(ctx: dict) -> None:
    """Fetch the details of linked series that are due, and store their VOD offsets.

    Runs only while ``RIOT_WEB_API_KEY`` is set.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Riot Web Details Cron")
    sessions = connections.subscription_sessions
    if not settings.RIOT_WEB_API_KEY or sessions is None:
        return
    if summary := await riot_web.refresh_details(sessions, ctx["redis"]):
        logger.info("riot details: %s", summary)


async def riot_web_brackets_cron(ctx: dict) -> None:
    """Refresh the standings of Riot tournaments whose linked series are playing now.

    Runs only while ``RIOT_WEB_API_KEY`` is set.

    :param ctx: arq context holding the shared Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Riot Web Brackets Cron")
    sessions = connections.subscription_sessions
    if not settings.RIOT_WEB_API_KEY or sessions is None:
        return
    if summary := await brackets.refresh_tournaments(sessions, ctx["redis"]):
        logger.info("riot brackets: %s", summary)
