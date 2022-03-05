import os
import sys
from datetime import datetime

from fastapi.testclient import TestClient
from firebase_admin import initialize_app, messaging

from app.main import app


def send():
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") is None:
        print(
            "Please set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to the path of the service account JSON"
        )
        sys.exit(1)

    client = TestClient(app)
    current_time = datetime.utcnow()
    upcoming_matches = [
        match
        for match in client.get("/api/v1/matches").json()
        if match["status"].lower() == "upcoming"
        and (datetime.fromisoformat(match["time"]) - current_time).total_seconds() < 900
    ]

    initialize_app()
    messages = []
    for match in upcoming_matches:
        print(f"Sending notification for {match=}")
        time_to_start = int((datetime.fromisoformat(match["time"]) - current_time).total_seconds() // 60)
        messages.append(
            messaging.Message(
                data={
                    "title": f"{match['team1']['name']} vs {match['team2']['name']}",
                    "body": f"Match is starting in {time_to_start} minutes",
                    "match_id": match["id"],
                },
                topic=f"match-{match['id']}",
            )
        )
    response = messaging.send_all(messages)
    print(f"{vars(response)=}")
