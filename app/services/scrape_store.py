"""Translate scraped VLR entities to and from SQLite rows. Never commits; the caller owns the transaction."""

import time
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.constants import IdMapKind
from app.db.models import IdMapping, Player, Team


async def upsert_player(session: AsyncSession, player_id: str, player: schemas.Player) -> None:
    """Store a scraped player page, keeping its first-seen time.

    :param session: Caller-owned database session.
    :param player_id: VLR player ID.
    :param player: Parsed player page.
    :return: None.
    """
    now = int(time.time())
    values = {
        "source": "vlr",
        "name": player.alias,
        "team_id": player.current_team.id if player.current_team else None,
        "country": player.country,
        "payload": player.model_dump(mode="json"),
        "last_fetched_at": now,
    }
    statement = insert(Player).values(id=player_id, first_seen_at=now, **values)
    await session.execute(statement.on_conflict_do_update(index_elements=[Player.id], set_=values))


async def player_fetch_times(session: AsyncSession, player_ids: Iterable[str]) -> dict[str, int]:
    """Return when each known player was last fetched.

    :param session: Caller-owned database session.
    :param player_ids: VLR player IDs.
    :return: Last fetch time (Unix seconds) keyed by player ID; unknown players are absent.
    """
    rows = await session.execute(select(Player.id, Player.last_fetched_at).where(Player.id.in_(list(player_ids))))
    return dict(rows.tuples().all())


async def player_team_ids(session: AsyncSession, player_ids: Iterable[str]) -> dict[str, str | None]:
    """Return the stored current team of each known player.

    :param session: Caller-owned database session.
    :param player_ids: VLR player IDs.
    :return: Team ID (or None when teamless) keyed by player ID; unknown players are absent.
    """
    rows = await session.execute(select(Player.id, Player.team_id).where(Player.id.in_(list(player_ids))))
    return dict(rows.tuples().all())


async def upsert_team(session: AsyncSession, team_id: str, **fields: str | int | None) -> None:
    """Store the team fields one scrape carries, keeping stored values for the fields it does not.

    :param session: Caller-owned database session.
    :param team_id: VLR team ID.
    :param fields: `Team` columns (e.g. `name`, `tag`, `rank`); a None value leaves the stored value.
    :return: None.
    """
    now = int(time.time())
    statement = insert(Team).values(id=team_id, source="vlr", first_seen_at=now, last_fetched_at=now, **fields)
    updates = {name: func.coalesce(statement.excluded[name], getattr(Team, name)) for name in fields}
    await session.execute(
        statement.on_conflict_do_update(index_elements=[Team.id], set_={**updates, "last_fetched_at": now})
    )


async def upsert_id_map(session: AsyncSession, kind: IdMapKind, mapping: dict[str, str]) -> None:
    """Store simplified names and the VLR IDs they resolve to.

    :param session: Caller-owned database session.
    :param kind: Whether the names are teams or events.
    :param mapping: VLR ID keyed by simplified name.
    :return: None.
    """
    for key, value in mapping.items():
        statement = insert(IdMapping).values(kind=kind, key=key, id=value)
        await session.execute(
            statement.on_conflict_do_update(index_elements=[IdMapping.kind, IdMapping.key], set_={"id": value})
        )
