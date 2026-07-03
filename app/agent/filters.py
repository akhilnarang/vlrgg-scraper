from typing import TypedDict

from pydantic import BaseModel

MAX_TOOL_RESULT_ROWS = 80


class MatchRow(TypedDict, total=False):
    """The subset of a serialized match dict that the filters read.

    Match dicts come from ``CompletedMatch``/``PlayerMatch`` ``model_dump(mode="json")``
    and carry more keys than this; these are the ones filtering operates on.
    """

    opponent: str
    event: str
    stage: str
    date: str
    score: str
    roster_core: str
    opponent_roster_core: str


class TruncatedResult(BaseModel):
    """Returned in place of a row list when it exceeds the per-tool budget."""

    truncated: bool = True
    total: int
    shown: int = 0
    hint: str = "Too many results. Ask the user to narrow the query, or call again with filters."
    available_filters: list[str]


def _contains(haystack: str, needle: str | None) -> bool:
    return needle is None or needle.lower() in (haystack or "").lower()


def filter_matches(
    matches: list[MatchRow],
    opponent: str | None = None,
    event: str | None = None,
    stage: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
    roster_core: str | None = None,
    opponent_roster_core: str | None = None,
) -> list[MatchRow]:
    out: list[MatchRow] = []
    for m in matches:
        if not _contains(m.get("opponent", ""), opponent):
            continue
        if not _contains(m.get("event", ""), event):
            continue
        if not _contains(m.get("stage", ""), stage):
            continue
        if not _contains(m.get("roster_core", ""), roster_core):
            continue
        if not _contains(m.get("opponent_roster_core", ""), opponent_roster_core):
            continue
        d = str(m.get("date", ""))[:10]
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        out.append(m)
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def guard_rows(rows: list[MatchRow], available_filters: list[str]) -> list[MatchRow] | TruncatedResult:
    if len(rows) > MAX_TOOL_RESULT_ROWS:
        return TruncatedResult(total=len(rows), available_filters=available_filters)
    return rows
