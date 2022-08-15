import asyncio
import sys
from asyncio import Task
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from arq import cron
from arq.worker import Worker, create_worker
from firebase_admin import initialize_app, messaging

from app.constants import MatchStatus
from app.core.config import settings
from app.services import matches


async def startup(_: dict) -> None:
    """
    To be run on startup of the cron
    :param _: Context dict
    :return: Nothing
    """
    # Initialize firebase
    initialize_app()


async def fcm_notification(_: dict) -> None:
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

        # Retrieve team IDs by querying the match information
        team1_id, team2_id = (team.id for team in (await matches.match_by_id(match.id)).teams)

        # Calculate the time left in minutes
        time_to_start = int((match.time - current_time).total_seconds() // 60)

        # Create the firebase message
        messages.append(
            messaging.Message(
                data={
                    "title": f"{match.team1.name} vs {match.team2.name}",
                    "body": f"Match is starting in {time_to_start} minutes",
                    "match_id": match.id,
                },
                # Even if a person has subscribed to the match + both teams, they shouldn't receive multiple
                # notifications
                condition=f"'match-{match.id}' in topics || 'team-{team1_id}' in topics || 'team-{team2_id}' in topics",
            ),
        )

    # Don't bother sending if there's nothing to send
    if messages:
        response = messaging.send_all(messages)
        print(f"{vars(response)=}")
    else:
        print("No notifications to send")


class ArqWorker:
    def __init__(self) -> None:
        self.worker: Worker | None = None
        self.task: Task | None = None

    async def start(self, **kwargs: Any) -> None:
        self.worker = create_worker(
            {
                "on_startup": startup,
                "cron_jobs": [cron("app.cron.fcm_notification", hour=None, minute={0, 15, 30, 45})],
            },
            **kwargs,
        )
        self.task = asyncio.create_task(self.worker.async_run())

    async def stop(self) -> None:
        if self.worker:
            await self.worker.close()


arq_worker = ArqWorker()
