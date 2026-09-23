"""Run Alembic migrations during application startup."""

import asyncio

from alembic.config import Config

from alembic import command
from app.db.engine import ensure_database_directory


async def upgrade_to_head(database_url: str) -> None:
    """Apply the current Alembic schema to the subscription database.

    :param database_url: SQLAlchemy database URL.
    :return: None.
    """
    ensure_database_directory(database_url)

    def upgrade() -> None:
        """Run synchronous Alembic migration in the worker thread.

        :return: None.
        """
        config = Config("alembic.ini")
        config.attributes["app"] = True
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")

    await asyncio.to_thread(upgrade)
