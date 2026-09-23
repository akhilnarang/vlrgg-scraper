"""Async SQLite engine setup for live-update subscriptions."""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def ensure_database_directory(database_url: str) -> None:
    """Create the parent directory for an on-disk SQLite database.

    :param database_url: SQLAlchemy database URL.
    :return: None.
    """
    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        Path(database_url.split("///", 1)[1]).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async engine with the SQLite pragmas this app relies on.

    :param database_url: SQLAlchemy database URL.
    :return: Configured async engine.
    """
    ensure_database_directory(database_url)
    engine = create_async_engine(database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """Configure each SQLite connection.

        WAL lets API reads run while the cron writes, busy_timeout waits for a
        held write lock instead of failing at once, NORMAL sync is durable
        enough under WAL, and foreign keys enforce the cascades.

        :param dbapi_connection: Raw database connection.
        :param _connection_record: SQLAlchemy pool connection record.
        :return: None.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
