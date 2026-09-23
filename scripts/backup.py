"""Back up the live SQLite database, including committed WAL changes."""

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def main() -> None:
    """Create and verify an atomic SQLite backup.

    :return: None.
    :raises SystemExit: If the database URL or backup path is invalid.
    :raises RuntimeError: If the backup fails its integrity check.
    """
    os.chdir(Path(__file__).resolve().parent.parent)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        nargs="?",
        default=f"backups/db-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite3",
        help="backup file path (default: backups/db-<UTC timestamp>.sqlite3)",
    )
    args = parser.parse_args()
    url = make_url(os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3"))
    if url.get_backend_name() != "sqlite":
        raise SystemExit("backup supports only SQLite DATABASE_URL values")
    if not url.database or url.database == ":memory:":
        raise SystemExit("DATABASE_URL must point to a SQLite file")

    source = Path(url.database).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    if source == destination:
        raise SystemExit("backup path must differ from the source database")
    if not source.is_file():
        raise SystemExit(f"source database does not exist: {source}")

    os.umask(0o077)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.chmod(0o600)

    try:
        source_uri = f"{source.as_uri()}?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source_db,
            closing(sqlite3.connect(temporary)) as backup_db,
        ):
            source_db.backup(backup_db)
            result = backup_db.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RuntimeError(f"backup integrity check failed: {result!r}")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    print(destination)


if __name__ == "__main__":
    main()
