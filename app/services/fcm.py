"""FCM topic and direct messages for compact live-match scores."""

import asyncio
import logging
from datetime import timedelta

from firebase_admin import App, credentials, delete_app, initialize_app, messaging
from firebase_admin import get_app as firebase_get_app

from app.core.config import settings
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import CompactState
from app.services import push
from app.services.push import Routing

_FCM_APP_NAME = "vlrgg-fcm"
logger = logging.getLogger(__name__)


class FCMError(Exception):
    """FCM delivery failure, with an indication that the device token is no longer valid."""

    def __init__(self, message: str, is_unregistered: bool = False):
        """Record the rejected delivery.

        :param message: Firebase rejection details.
        :param is_unregistered: Whether Firebase rejected the registration token.
        :return: None.
        """
        super().__init__(message)
        self.is_unregistered = is_unregistered


def get_app() -> App:
    """Reuse or initialize the named Firebase app.

    :return: Initialized Firebase app.
    """
    try:
        return firebase_get_app(_FCM_APP_NAME)
    except ValueError:
        # Not initialized yet; nothing awaits in between, so no second caller can race this.
        return initialize_app(credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS), name=_FCM_APP_NAME)


async def deliver_start(client_id: str, token: str, state: CompactState) -> None:
    """Deliver an immediate live-match start to an Android device.

    :param client_id: Client UUID.
    :param token: FCM registration token.
    :param state: Current compact match state.
    :return: None.
    :raises ServiceUnavailableError: If FCM is unavailable or rejects the start.
    """
    if not settings.GOOGLE_APPLICATION_CREDENTIALS:
        raise ServiceUnavailableError("FCM client is not configured")
    try:
        fcm_app = get_app()
    except Exception as exc:
        logger.warning("FCM initialization failed: %r", exc)
        raise ServiceUnavailableError("FCM client is not configured") from exc
    if fcm_app is None:
        raise ServiceUnavailableError("FCM client is not configured")
    try:
        await send_to_token(fcm_app, token, state)
    except FCMError as exc:
        logger.warning("FCM start failed for client %s, match %s: %r", client_id, state.match_id, exc)
        if exc.is_unregistered:
            await push.clear_rejected_token(token)
        raise ServiceUnavailableError("FCM start failed") from exc


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
    # Released apps show a notification for any message on the legacy `match-`/`event-`/`team-` topics.
    topics = [f"live-match-{routing.match_id}"]
    if routing.event_id:
        topics.append(f"live-event-{routing.event_id}")
    topics.extend(f"live-team-{value}" for value in routing.team_ids)
    topics.extend(f"live-player-{value}" for value in player_ids)
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


def build_direct_message(token: str, state: CompactState) -> messaging.Message:
    """Build a targeted, data-only FCM unicast message for an Android device token.

    Constructs a high-priority data payload containing the live match's compact score state.
    Sending as data-only bypasses default Android system notification tray rendering, allowing
    the client app's background message handler to immediately instantiate or update a Rich
    Ongoing Notification (RON) or heads-up notification.

    The message uses a collapse key scoped to the match ID (``match-{match_id}``) to coalesce
    rapidly succeeding score ticks and avoid notification queue stacking. Its TTL is set to
    120 seconds for in-progress matches to prevent stale updates after reconnection, and 8 hours
    for terminal match states.

    :param token: Target device FCM registration token.
    :param state: Projected compact match score state.
    :return: Formatted Firebase unicast Message object.
    """
    ttl = timedelta(hours=8) if state.terminal else timedelta(seconds=120)
    android = messaging.AndroidConfig(collapse_key=f"match-{state.match_id}", priority="high", ttl=ttl)
    return messaging.Message(
        token=token,
        data={"type": "match-live-v1", "state": state.model_dump_json()},
        android=android,
    )


async def send_to_token(app: App, token: str, state: CompactState) -> None:
    """Send an immediate compact score state directly to an FCM device token.

    :param app: Firebase application.
    :param token: Target device FCM registration token.
    :param state: Compact score state.
    :return: None.
    :raises FCMError: If Firebase rejects the message.
    """
    response = await messaging.send_each_async(messages=[build_direct_message(token, state)], dry_run=False, app=app)
    if response.failure_count:
        exc = response.responses[0].exception
        raise FCMError(str(exc), is_unregistered=isinstance(exc, messaging.UnregisteredError)) from exc


async def publish(app: App, messages: list[messaging.Message]) -> list[str]:
    """Send a batch of score messages through Firebase.

    :param app: Firebase application.
    :param messages: Topic messages to send.
    :return: Firebase message IDs, one per message.
    :raises RuntimeError: If Firebase rejects any message.
    """
    response = await messaging.send_each_async(messages=messages, dry_run=False, app=app)
    if response.failure_count:
        raise RuntimeError("FCM rejected a live match message")
    return [result.message_id for result in response.responses]
