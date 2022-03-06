import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from firebase_admin import initialize_app, messaging

from app.constants import MatchStatus
from app.services import matches


async def send() -> None:
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") is None:
        print(
            "Please set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to the path of the service account "
            "JSON "
        )
        sys.exit(1)

    current_time = datetime.now(tz=ZoneInfo("UTC"))
    upcoming_matches = [
        match
        for match in await matches.get_upcoming_matches()
        if match.status == MatchStatus.UPCOMING and (match.time - current_time).total_seconds() < 900
    ]

    initialize_app()
    messages = []
    for match in upcoming_matches:
        print(f"Sending notification for {match=}")
        time_to_start = int((match.time - current_time).total_seconds() // 60)
        messages.append(
            messaging.Message(
                data={
                    "title": f"{match.team1.name} vs {match.team2.name}",
                    "body": f"Match is starting in {time_to_start} minutes",
                    "match_id": match.id,
                },
                topic=f"match-{match.id}",
            )
        )
    if messages:
        response = messaging.send_all(messages)
        print(f"{vars(response)=}")
    else:
        print("No notifications to send")
