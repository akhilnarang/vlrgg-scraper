"""Copy the retired SQLite database into an empty, migrated PostgreSQL database.

Run once at cutover, with the app stopped so the SQLite file no longer changes:

    DATABASE_URL=postgresql+asyncpg:///vlrgg uv run python -m scripts.sqlite_to_postgres db.sqlite3
"""

import argparse
import asyncio
import json
import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from sqlalchemy import Boolean, Date, Integer, func, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.migrations import upgrade_to_head
from app.db.models import Base

BATCH_SIZE = 5000


def convert(column, value):
    """Turn one SQLite value into the Python value its PostgreSQL column takes.

    :param column: Target SQLAlchemy column.
    :param value: Value as SQLite returned it (JSONB already rendered to text by json()).
    :return: Converted value.
    """
    if value is None:
        return None
    if isinstance(column.type, JSONB):
        return json.loads(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Boolean):
        return bool(value)
    return value


async def copy(source: Path) -> None:
    """Copy every model table from ``source`` into ``settings.DATABASE_URL`` in one transaction.

    :param source: SQLite database file.
    :return: None.
    :raises SystemExit: If the SQLite schema does not match the models.
    """
    await upgrade_to_head(settings.DATABASE_URL)
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as sqlite_db:
            async with engine.begin() as connection:
                for table in Base.metadata.sorted_tables:
                    source_columns = {row[1] for row in sqlite_db.execute(f"PRAGMA table_info({table.name})")}
                    if source_columns != set(table.columns.keys()):
                        raise SystemExit(f"{table.name}: SQLite columns {sorted(source_columns)} differ from the model")
                    columns = list(table.columns)
                    # json() renders SQLite's binary JSONB back to text.
                    selected = ", ".join(
                        f"json({column.name})" if isinstance(column.type, JSONB) else column.name for column in columns
                    )
                    cursor = sqlite_db.execute(f"SELECT {selected} FROM {table.name}")
                    copied = 0
                    while rows := cursor.fetchmany(BATCH_SIZE):
                        await connection.execute(
                            insert(table),
                            [
                                {column.name: convert(column, value) for column, value in zip(columns, row)}
                                for row in rows
                            ],
                        )
                        copied += len(rows)
                    target = await connection.scalar(select(func.count()).select_from(table))
                    if target != copied:
                        raise SystemExit(f"{table.name}: copied {copied} rows but the target holds {target}")
                    for column in columns:
                        # Explicit IDs bypass the sequence, so move it past the copied rows.
                        if column.autoincrement is True and isinstance(column.type, Integer):
                            await connection.execute(
                                text(
                                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column.name}'), "
                                    f"COALESCE(MAX({column.name}), 0) + 1, false) FROM {table.name}"
                                )
                            )
                    print(f"{table.name}: {copied}")
    finally:
        await engine.dispose()


def main() -> None:
    """Parse arguments and run the copy.

    :return: None.
    """
    os.chdir(Path(__file__).resolve().parent.parent)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="SQLite database file")
    asyncio.run(copy(parser.parse_args().source))


if __name__ == "__main__":
    main()
