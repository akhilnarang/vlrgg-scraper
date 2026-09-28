"""FCM direct messages for compact live-match scores."""

import asyncio
import logging
from datetime import timedelta

from firebase_admin import App, credentials, delete_app, initialize_app, messaging
from firebase_admin import get_app as firebase_get_app

from app.core.config import settings
from app.exceptions import ServiceUnavailableError
from app.schemas.matches import CompactState
from app.services import push

_FCM_APP_NAME = "vlrgg-fcm"
# Firebase rejects a send_each_async call with more messages than this, whole batch included.
_FCM_MAX_BATCH = 500
# Per-token errors that will never succeed on retry, so the token must be removed:
# an unregistered token, or one registered to a different Firebase project.
_PERMANENT_TOKEN_ERRORS = (messaging.UnregisteredError, messaging.SenderIdMismatchError)
logger = logging.getLogger(__name__)


class FCMError(Exception):
    """FCM delivery failure, with an indication that the device token is no longer valid."""

    def __init__(self, message: str, is_dead_token: bool = False):
        """Record the rejected delivery.

        :param message: Firebase rejection details.
        :param is_dead_token: Whether the registration token is permanently invalid.
        :return: None.
        """
        super().__init__(message)
        self.is_dead_token = is_dead_token


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
        if exc.is_dead_token:
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


def build_direct_message(token: str, state: CompactState, *, collapse: bool = True) -> messaging.Message:
    """Build a data-only FCM message for an Android device token.

    Data-only payloads let client background handlers manage notification display.
    Match starts use a collapse key to avoid queue stacking; score updates are not
    collapsed so intermediate states are delivered.

    :param token: Target device FCM registration token.
    :param state: Projected compact match score state.
    :param collapse: Whether to set a match-scoped collapse key.
    :return: Formatted Firebase Message object.
    """
    ttl = timedelta(hours=8) if state.terminal else timedelta(seconds=120)
    android = messaging.AndroidConfig(
        collapse_key=f"match-{state.match_id}" if collapse else None, priority="high", ttl=ttl
    )
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
        raise FCMError(str(exc), is_dead_token=isinstance(exc, _PERMANENT_TOKEN_ERRORS)) from exc


async def publish_direct(app: App, tokens: list[str], state: CompactState) -> list[str]:
    """Send score updates to follower device tokens, clearing permanently dead tokens.

    Tokens are sent in Firebase-sized batches; an unregistered token or one belonging
    to another Firebase project is cleared, and any other failure is raised so the
    caller does not treat the send as delivered.

    :param app: Firebase application.
    :param tokens: Follower FCM registration tokens.
    :param state: Compact score state.
    :return: Message IDs of accepted messages.
    :raises FCMError: If Firebase rejected messages for any reason other than a dead token.
    """
    message_ids: list[str] = []
    failures = 0
    first_error: Exception | None = None
    for start in range(0, len(tokens), _FCM_MAX_BATCH):
        batch = tokens[start : start + _FCM_MAX_BATCH]
        response = await messaging.send_each_async(
            messages=[build_direct_message(token, state, collapse=False) for token in batch], dry_run=False, app=app
        )
        for token, result in zip(batch, response.responses, strict=True):
            if result.exception is None:
                message_ids.append(result.message_id)
            elif isinstance(result.exception, _PERMANENT_TOKEN_ERRORS):
                await push.clear_rejected_token(token)
            else:
                failures += 1
                if first_error is None:
                    first_error = result.exception
    if first_error is not None:
        raise FCMError(f"{failures} FCM message(s) failed: {first_error!r}")
    return message_ids
