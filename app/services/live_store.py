"""SQLite store for a live match's VLR details and tracker score. Never commits; the caller owns the transaction."""

import time

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LiveMatchRecord
from app.utils import is_final


async def upsert_live_detail(session: AsyncSession, match_id: str, detail: dict, fetched_at: int) -> None:
    """Store a match's fetched VLR details, keeping its tracker score.

    :param session: Caller-owned database session.
    :param match_id: Match identifier.
    :param detail: Match details as JSON-ready data.
    :param fetched_at: When the details were fetched (Unix seconds).
    :return: None.
    """
    now = int(time.time())
    values = {"detail": detail, "detail_fetched_at": fetched_at, "updated_at": now}
    statement = insert(LiveMatchRecord).values(match_id=match_id, **values)
    await session.execute(statement.on_conflict_do_update(index_elements=[LiveMatchRecord.match_id], set_=values))


async def upsert_live_video(session: AsyncSession, match_id: str, video: dict, received_at: int) -> None:
    """Store the tracker's score for a match, keeping its VLR details.

    :param session: Caller-owned database session.
    :param match_id: Match identifier.
    :param video: Tracker score as JSON-ready data.
    :param received_at: When the score was received (Unix seconds).
    :return: None.
    """
    now = int(time.time())
    values = {"video": video, "video_received_at": received_at, "updated_at": now}
    statement = insert(LiveMatchRecord).values(match_id=match_id, **values)
    await session.execute(statement.on_conflict_do_update(index_elements=[LiveMatchRecord.match_id], set_=values))


async def get_live_match(session: AsyncSession, match_id: str) -> LiveMatchRecord | None:
    """Read a match's stored details and tracker score.

    :param session: Caller-owned database session.
    :param match_id: Match identifier.
    :return: Stored live match, or None.
    """
    return await session.get(LiveMatchRecord, match_id)


async def live_details(session: AsyncSession) -> dict[str, dict]:
    """Read every stored live match's details, for the video push's fallback when its cache is cold.

    A completed match, or one whose details were retired before their deletion, is excluded even when
    its row survived a failed cleanup, so the fallback can never resolve it, re-announce a final, or
    resurrect a finished match.

    :param session: Caller-owned database session.
    :return: Details as JSON-ready data keyed by match ID; matches without details are absent.
    """
    rows = await session.execute(
        select(LiveMatchRecord.match_id, LiveMatchRecord.detail).where(LiveMatchRecord.detail.is_not(None))
    )
    return {
        match_id: detail
        for match_id, detail in rows.all()
        if detail is not None and not is_final(detail.get("event", {}).get("status"))
    }


async def retire_live_match(session: AsyncSession, match_id: str) -> None:
    """Drop a match's stored details ahead of their deletion, so a failed cleanup cannot leave them live.

    :param session: Caller-owned database session.
    :param match_id: Match identifier.
    :return: None.
    """
    await session.execute(
        update(LiveMatchRecord)
        .where(LiveMatchRecord.match_id == match_id)
        .values(detail=None, detail_fetched_at=None, updated_at=int(time.time()))
    )


async def delete_live_match(session: AsyncSession, match_id: str) -> None:
    """Remove a finished match's stored details and tracker score.

    :param session: Caller-owned database session.
    :param match_id: Match identifier.
    :return: None.
    """
    await session.execute(delete(LiveMatchRecord).where(LiveMatchRecord.match_id == match_id))
