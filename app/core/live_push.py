"""Startup and shutdown for the live-push subscription store and APNs client."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import connections
from app.core.config import settings
from app.db.engine import create_engine
from app.db.migrations import upgrade_to_head
from app.services import apns, fcm


async def start_live_push() -> None:
    """Migrate and open live-push database sessions and the APNs client.

    :return: None.
    :raises RuntimeError: If live push has no API keys configured.
    :raises ValueError: If the Firebase service account is invalid.
    """
    if not settings.API_KEYS:
        raise RuntimeError("ENABLE_LIVE_PUSH requires API_KEYS")
    await upgrade_to_head(settings.DATABASE_URL)
    connections.database_engine = create_engine(settings.DATABASE_URL)
    connections.subscription_sessions = async_sessionmaker(connections.database_engine, expire_on_commit=False)
    connections.apns_client = apns.build_client()
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        fcm.get_app()  # fail at startup on a bad service account, not on every cron run


async def stop_live_push() -> None:
    """Close live-push connections when present.

    :return: None.
    """
    if connections.apns_client is not None:
        await connections.apns_client.aclose()
        connections.apns_client = None
    if connections.database_engine is not None:
        await connections.database_engine.dispose()
        connections.database_engine = None
        connections.subscription_sessions = None
