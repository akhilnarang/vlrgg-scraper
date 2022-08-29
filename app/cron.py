import asyncio
import json
import sys
from asyncio import Task
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pydantic.json
from arq import cron
from arq.worker import Worker, create_worker
from firebase_admin import delete_app, initialize_app, messaging

from app.cache import cache
from app.constants import MatchStatus
from app.core.config import settings
from app.services import events, matches, news, rankings


async def fcm_notification_cron(_: dict) -> None:
    """
    Function to notify users about upcoming matches
    :param _: Context dict
    :return: Nothing
    """
    # Ensure that env is setup
    if settings.GOOGLE_APPLICATION_CREDENTIALS is None:
        print(
            "Please set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to the path of the service account "
            "JSON "
        )
        sys.exit(1)

    # Get the current time, so that we can filter for matches starting in the next 15 minutes
    current_time = datetime.now(tz=ZoneInfo("UTC"))
    upcoming_matches = [
        match
        for match in await matches.get_upcoming_matches()
        if match.status == MatchStatus.UPCOMING and (match.time - current_time).total_seconds() < 900
    ]

    # Initialize an empty list of messages
    messages = []

    # Iterate over upcoming matches
    for match in upcoming_matches:
        print(f"Sending notification for {match=}")

        match_details = await matches.match_by_id(match.id)

        # Retrieve team IDs by querying the match information
        team1_id, team2_id = (team.id for team in match_details.teams)

        # Calculate the time left in minutes
        time_to_start = int((match.time - current_time).total_seconds() // 60)

        # Define the notification payload
        payload = {
            "title": f"{match.team1.name} vs {match.team2.name}",
            "body": f"Match is starting in {time_to_start} minutes",
            "match_id": match.id,
        }
        streams = match_details.videos.streams

        # Create the firebase message
        messages.append(
            messaging.Message(
                data=payload | {"stream_url": streams[0] if len(streams) > 0 else None},
                # Even if a person has subscribed to the event + match + both teams, they shouldn't receive multiple
                # notifications
                condition=f"'event-{match_details.event.id}' || 'match-{match.id}' in topics || 'team-{team1_id}' in "
                f"topics || 'team-{team2_id}' in topics",
            ),
        )

    # Don't bother sending if there's nothing to send
    if messages:
        app = initialize_app()
        response = messaging.send_all(messages)
        delete_app(app)
        print(f"{vars(response)=}")
    else:
        print("No notifications to send")


async def rankings_cron(ctx: dict) -> None:
    """
    Function to fetch rankings from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    response = await rankings.ranking_list()
    await ctx.get("redis", cache.get_client()).set(
        "rankings", json.dumps([item.dict() for item in response], default=pydantic.json.pydantic_encoder)
    )


async def matches_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    response = await matches.match_list()
    await ctx.get("redis", cache.get_client()).set(
        "matches", json.dumps([item.dict() for item in response], default=pydantic.json.pydantic_encoder)
    )


async def events_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    response = await events.get_events()
    await ctx.get("redis", cache.get_client()).set(
        "events", json.dumps([item.dict() for item in response], default=pydantic.json.pydantic_encoder)
    )


async def news_cron(ctx: dict) -> None:
    """
    Function to fetch matches from VLR and update the cache
    :param ctx: Context dict
    :return: Nothing
    """
    response = await news.news_list()
    await ctx.get("redis", cache.get_client()).set(
        "news", json.dumps([item.dict() for item in response], default=pydantic.json.pydantic_encoder)
    )


class ArqWorker:
    def __init__(self) -> None:
        self.worker: Worker | None = None
        self.task: Task | None = None

    async def start(self, **kwargs: Any) -> None:
        self.worker = create_worker(
            {
                "cron_jobs": [
                    cron("app.cron.fcm_notification_cron", hour=None, minute={0, 15, 30, 45}),
                    cron("app.cron.rankings_cron", hour=None, minute={0, 30}),
                    cron("app.cron.matches_cron", hour=None, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
                    cron("app.cron.events_cron", hour=None, minute={0, 30}),
                    cron("app.cron.news_cron", hour=None, minute={0, 30}),
                ],
            },
            **kwargs,
        )
        self.task = asyncio.create_task(self.worker.async_run())

    async def stop(self) -> None:
        if self.worker:
            await self.worker.close()


arq_worker = ArqWorker()
