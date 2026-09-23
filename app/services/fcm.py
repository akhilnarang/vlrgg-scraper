"""FCM topic messages for compact live-match scores."""

import asyncio
import logging
from datetime import timedelta

from firebase_admin import App, credentials, delete_app, initialize_app, messaging
from firebase_admin import get_app as firebase_get_app

from app.core.config import settings
from app.schemas.matches import CompactState
from app.services.push import Routing

_FCM_APP_NAME = "vlrgg-fcm"
logger = logging.getLogger(__name__)


def get_app() -> App:
    """Reuse or initialize the named Firebase app.

    :return: Initialized Firebase app.
    """
    try:
        return firebase_get_app(_FCM_APP_NAME)
    except ValueError:
        # Not initialized yet; nothing awaits in between, so no second caller can race this.
        return initialize_app(credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS), name=_FCM_APP_NAME)


async def close_app() -> None:
    """Delete the Firebase app outside the event loop.

    :return: None.
    """
    # firebase_admin 7.3.0 closes an async HTTP client via asyncio.run(), so
    # cleanup must happen off the worker's event loop.
    try:
        await asyncio.to_thread(delete_app, firebase_get_app(_FCM_APP_NAME))
    except ValueError:
        return  # never initialized
    except Exception:
        logger.exception("Failed to delete Firebase app during shutdown: %s", _FCM_APP_NAME)


def build_messages(state: CompactState, routing: Routing, player_ids: list[str]) -> list[messaging.Message]:
    """Build data-only FCM topic messages for a match score.

    :param state: Compact score state.
    :param routing: Match, event, and team IDs for topics.
    :param player_ids: Player IDs that have at least one stored favorite.
    :return: FCM messages with per-match collapse keys.
    """
    topics = [f"match-{routing.match_id}"]
    if routing.event_id:
        topics.append(f"event-{routing.event_id}")
    topics.extend(f"team-{value}" for value in routing.team_ids)
    topics.extend(f"player-{value}" for value in player_ids)
    topics = list(dict.fromkeys(topics))
    ttl = timedelta(hours=8) if state.terminal else timedelta(seconds=120)
    android = messaging.AndroidConfig(collapse_key=f"match-{state.match_id}", priority="high", ttl=ttl)
    data = {"type": "match-live-v1", "state": state.model_dump_json()}
    return [
        messaging.Message(
            condition=" || ".join(f"'{topic}' in topics" for topic in topics[index : index + 5]),
            data=data,
            android=android,
        )
        for index in range(0, len(topics), 5)
    ]


async def publish(app: App, messages: list[messaging.Message]) -> None:
    """Send a batch of score messages through Firebase.

    :param app: Firebase application.
    :param messages: Topic messages to send.
    :return: None.
    :raises RuntimeError: If Firebase rejects any message.
    """
    response = await messaging.send_each_async(messages=messages, dry_run=False, app=app)
    if response.failure_count:
        raise RuntimeError("FCM rejected a live match message")
