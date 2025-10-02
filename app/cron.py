import asyncio
import json
import logging
import uuid
from asyncio import Task
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from arq import cron
from arq.worker import Worker, create_worker
from fastapi.encoders import jsonable_encoder
from firebase_admin import credentials, delete_app, initialize_app, messaging
from sentry_sdk import get_current_scope

from app.constants import MatchStatus
from app.core.config import settings
from app.services import events, matches, news, rankings, standings


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

    # Initialize an empty list of messages
    messages = []

    # Iterate over upcoming matches
    for match in upcoming_matches:
        logging.info(f"Sending notification for {match=}")

        match_details = await matches.match_by_id(match.id, redis_client=client)

        # Retrieve team IDs by querying the match information
        team1_id, team2_id = (team.id for team in match_details.teams)

        # Calculate the time left in minutes
        time_to_start = int((match.time - current_time).total_seconds() // 60)

        # Define the notification payload
        payload = {
            "title": f"{match.team1.name} vs {match.team2.name}",
            "body": f"Match is starting in {time_to_start} minutes",
            "timestamp": match.time.isoformat(),
            "match_id": match.id,
        }
        if streams := match_details.videos.streams:
            payload |= {"stream_url": streams[0].url.unicode_string()}

        # Create the firebase message
        messages.append(
            messaging.Message(
                data=payload,
                # Even if a person has subscribed to the event + match + both teams, they shouldn't receive multiple
                # notifications
                condition=f"'event-{match_details.event.id}' in topics || 'match-{match.id}' in topics || "
                f"'team-{team1_id}' in topics || 'team-{team2_id}' in topics",
                android=messaging.AndroidConfig(ttl=timedelta(minutes=30)),
            ),
        )

    # Don't bother sending if there's nothing to send
    if messages:
        app_name = ctx.get("job_id", uuid.uuid4())
        app = initialize_app(
            name=app_name,
            credential=credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS),
        )
        messaging.send_each(messages=messages, app=app)
        delete_app(app)
        logging.info("Sent notification")
    else:
        logging.info("No notifications to send")


async def rankings_cron(ctx: dict) -> None:
    """
    Function to fetch rankings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Rankings Cron")

    await ctx["redis"].set(
        "rankings",
        json.dumps(
            [item.model_dump() for item in await rankings.ranking_list()],
            default=jsonable_encoder,
        ),
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
        json.dumps(
            [item.model_dump() for item in await matches.match_list(redis_client=client)],
            default=jsonable_encoder,
        ),
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
        json.dumps(
            [item.model_dump() for item in await events.get_events(cache_client=client)],
            default=jsonable_encoder,
        ),
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
        json.dumps(
            [item.model_dump() for item in await news.news_list()],
            default=jsonable_encoder,
        ),
    )


async def standings_cron(ctx: dict) -> None:
    """
    Function to fetch standings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    get_current_scope().set_transaction_name("Standings Cron")
    client = ctx["redis"]
    current_year = datetime.now().year

    result = await standings.standings_list(current_year)
    await client.set(
        f"standings_{current_year}",
        result.model_dump_json(),
        ex=3600,  # 1 hour TTL
    )


class ArqWorker:
    def __init__(self) -> None:
        self.worker: Worker | None = None
        self.task: Task | None = None

    async def start(self, **kwargs: Any) -> None:
        cron_jobs = [
            cron("app.cron.rankings_cron", hour=None, minute={0, 30}),
            cron(
                "app.cron.matches_cron",
                hour=None,
                minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            ),
            cron("app.cron.events_cron", hour=None, minute={0, 30}),
            cron("app.cron.news_cron", hour=None, minute={0, 30}),
            cron("app.cron.standings_cron", hour=0, minute=0),
        ]

        # Only try to run the FCM cron if we have a service account JSON
        if settings.GOOGLE_APPLICATION_CREDENTIALS is not None:
            cron_jobs.append(cron("app.cron.fcm_notification_cron", hour=None, minute={0, 15, 30, 45}))

        self.worker = create_worker(
            {"cron_jobs": cron_jobs},
            **kwargs,
        )
        self.task = asyncio.create_task(self.worker.async_run())

    async def stop(self) -> None:
        if self.worker:
            await self.worker.close()


arq_worker = ArqWorker()
