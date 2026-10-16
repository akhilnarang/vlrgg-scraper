"""Write-once storage of each match's prediction and summaries."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MatchInsightRecord


async def get(session: AsyncSession, match_id: str) -> MatchInsightRecord | None:
    """Read a match's stored insights.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :return: The stored row, or None when nothing was stored for the match.
    """
    return await session.get(MatchInsightRecord, match_id)


async def _store(session: AsyncSession, match_id: str, column: str, payload: dict, at: datetime) -> None:
    """Store one artifact and its time unless the match already has that artifact.

    Payload and time are written by one conditional upsert, so concurrent writers
    cannot replace the first stored artifact.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :param column: Name of the artifact's payload column; its time column is ``<column>_at``.
    :param payload: Artifact to store.
    :param at: Time the artifact was generated.
    :return: None.
    """
    statement = insert(MatchInsightRecord).values({"match_id": match_id, column: payload, f"{column}_at": at})
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[MatchInsightRecord.match_id],
            set_={column: statement.excluded[column], f"{column}_at": statement.excluded[f"{column}_at"]},
            where=MatchInsightRecord.__table__.c[column].is_(None),
        )
    )


async def store_prediction(session: AsyncSession, match_id: str, payload: dict, at: datetime) -> None:
    """Store a match's model prediction unless one is stored.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :param payload: Win probabilities with the team IDs they describe.
    :param at: Time the prediction was made.
    :return: None.
    """
    await _store(session, match_id, "prediction", payload, at)


async def store_pre_summary(session: AsyncSession, match_id: str, payload: dict, at: datetime) -> None:
    """Store a match's pre-match summary unless one is stored.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :param payload: Summary text with its model, prompt version, and team IDs.
    :param at: Time the summary was generated.
    :return: None.
    """
    await _store(session, match_id, "pre_summary", payload, at)


async def store_post_summary(session: AsyncSession, match_id: str, payload: dict, at: datetime) -> None:
    """Store a match's post-match summary unless one is stored.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :param payload: Summary text with its model, prompt version, and team IDs.
    :param at: Time the summary was generated.
    :return: None.
    """
    await _store(session, match_id, "post_summary", payload, at)
