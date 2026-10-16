"""Source-backed head-to-head series records from the ranking ledger.

Ported from the prediction model's historical queries. Records are grouped by meta
scope (the match's ``major.minor`` patch, else its event and year) and are observed
frequencies, not forecasts.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MatchRecord

_PATCH = re.compile(r"^(\d+)\.(\d+)")


@dataclass(frozen=True)
class HistoryFilter:
    """Ledger results a query covers.

    :param from_date: First match day included, as the ledger dates matches.
    :param through_date: Last match day included.
    :param exclude_match_id: A match left out, such as the one being previewed.
    """

    from_date: date
    through_date: date
    exclude_match_id: str | None = None


def canonical_patch(patch: str | None) -> str | None:
    """Reduce a patch to its ``major.minor`` form without leading zeros.

    :param patch: Patch as VLR shows it, such as ``10.04``.
    :return: The canonical patch, or None when the value does not start with ``major.minor``.
    """
    match = _PATCH.match(patch or "")
    return f"{int(match[1])}.{int(match[2])}" if match else None


def _meta_scope(patch: str | None, event_id: str, played_on: date) -> str:
    """Name the meta a match was played in.

    :param patch: Stored patch.
    :param event_id: Match's event ID, scoping a match without a patch.
    :param played_on: Match day.
    :return: ``patch:<major.minor>``, else ``unknown:event:<event_id>:<year>``.
    """
    canonical = canonical_patch(patch)
    return f"patch:{canonical}" if canonical else f"unknown:event:{event_id}:{played_on.year}"


def _involving(team_id: str) -> ColumnElement[bool]:
    """Match a stored match either of whose sides is a team.

    :param team_id: Team ID.
    :return: SQL condition on ``matches``.
    """
    return or_(MatchRecord.team_a_id == team_id, MatchRecord.team_b_id == team_id)


async def head_to_head(
    session: AsyncSession, team_id: str, opponent_team_id: str, history: HistoryFilter
) -> list[dict]:
    """Report a team's decided series against another, by meta scope.

    :param session: Caller-owned database session.
    :param team_id: Team the records are counted for.
    :param opponent_team_id: Opponent's team ID.
    :param history: Query filter.
    :return: Each meta scope with the team's wins, losses, count, and win rate, in scope order.
    """
    conditions = [
        MatchRecord.played_on.between(history.from_date, history.through_date),
        _involving(team_id),
        _involving(opponent_team_id),
        MatchRecord.team_a_score != MatchRecord.team_b_score,
    ]
    if history.exclude_match_id is not None:
        conditions.append(MatchRecord.id != history.exclude_match_id)
    rows = await session.execute(
        select(
            MatchRecord.played_on,
            MatchRecord.event_id,
            MatchRecord.patch,
            MatchRecord.team_a_id,
            MatchRecord.team_a_score,
            MatchRecord.team_b_score,
        ).where(*conditions)
    )
    outcomes: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        won = (row.team_a_score > row.team_b_score) == (row.team_a_id == team_id)
        outcomes[_meta_scope(row.patch, row.event_id, row.played_on)].append(won)
    return [
        {
            "meta_scope": meta_scope,
            "record": {
                "wins": sum(won),
                "losses": len(won) - sum(won),
                "n": len(won),
                "win_rate": sum(won) / len(won),
            },
        }
        for meta_scope, won in sorted(outcomes.items())
    ]
