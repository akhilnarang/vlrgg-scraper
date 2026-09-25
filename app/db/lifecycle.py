"""Startup and shutdown for the SQLite database, independent of live push."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import connections
from app.core.config import settings
from app.db.engine import create_engine
from app.db.migrations import upgrade_to_head


async def start_database() -> None:
    """Migrate the database and open the shared session factory.

    :return: None.
    """
    await upgrade_to_head(settings.DATABASE_URL)
    connections.database_engine = create_engine(settings.DATABASE_URL)
    connections.subscription_sessions = async_sessionmaker(connections.database_engine, expire_on_commit=False)


async def stop_database() -> None:
    """Dispose of the database engine when present.

    :return: None.
    """
    if connections.database_engine is not None:
        await connections.database_engine.dispose()
        connections.database_engine = None
        connections.subscription_sessions = None
