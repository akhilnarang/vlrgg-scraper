"""Startup and shutdown for the live-push APNs client and Firebase app."""

from app.core import connections
from app.core.config import settings
from app.services import apns, fcm, team_logos


async def start_live_push() -> None:
    """Open the APNs client and validate Firebase credentials for live push.

    :return: None.
    :raises RuntimeError: If live push has no API keys configured or the database is not started.
    :raises ValueError: If the Firebase service account is invalid.
    """
    if not settings.API_KEYS:
        raise RuntimeError("ENABLE_LIVE_PUSH requires API_KEYS")
    if connections.subscription_sessions is None:
        raise RuntimeError("ENABLE_LIVE_PUSH requires the database to be started")
    connections.apns_client = apns.build_client()
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        fcm.get_app()  # fail at startup on a bad service account, not on every cron run
    await team_logos.load()


async def stop_live_push() -> None:
    """Close the APNs client when present.

    :return: None.
    """
    if connections.apns_client is not None:
        await connections.apns_client.aclose()
        connections.apns_client = None
