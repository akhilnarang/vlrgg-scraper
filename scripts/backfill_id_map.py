"""Copy the Redis team and event ID maps into SQLite's id_map table.

The scraper keys both Redis hashes (`team`, `event`) by simplified name and
keeps writing them; this copies what they hold today. Rerun at any time: rows
are upserted. Run where the app runs, after startup has applied migrations.

Usage (from the repo root): uv run python -m scripts.backfill_id_map
"""

import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.constants import IdMapKind
from app.core.config import settings
from app.db.engine import create_engine
from app.services import scrape_store


async def main() -> None:
    """Upsert every Redis ID map entry into id_map in one transaction.

    :return: None.
    """
    redis = Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, password=settings.REDIS_PASSWORD, decode_responses=True
    )
    engine = create_engine(settings.DATABASE_URL)
    try:
        async with async_sessionmaker(engine).begin() as session:
            for kind in IdMapKind:
                mapping = await redis.hgetall(kind)  # type: ignore
                await scrape_store.upsert_id_map(session, kind, mapping)
                print(f"{kind}: {len(mapping)} names")
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
