from app.agent.filters import MAX_TOOL_RESULT_ROWS, MatchRow, TruncatedResult, filter_matches, guard_rows

MATCH: MatchRow = {
    "opponent": "Team Vitality",
    "event": "Masters London 2026",
    "stage": "Playoffs ⋅GF",
    "date": "2026-06-10T17:00:00+00:00",
    "roster_core": "#ACM",
    "opponent_roster_core": "#YAJ",
}


def test_match_filters_return_the_requested_slice():
    matches: list[MatchRow] = [
        {**MATCH, "opponent": "LOUD"},
        {**MATCH, "event": "VCT 26: AMER"},
        {**MATCH, "stage": "Group Stage ⋅W1"},
        {**MATCH, "date": "2025-12-31T17:00:00+00:00"},
        {**MATCH, "date": "2027-01-01T17:00:00+00:00"},
        {**MATCH, "roster_core": "#OLD"},
        {**MATCH, "opponent_roster_core": "#Z5K"},
        MATCH,
        dict(MATCH),
    ]
    result = filter_matches(
        matches,
        opponent="vitality",
        event="london",
        stage="GF",
        date_from="2026-01-01",
        date_to="2026-12-31",
        roster_core="#ACM",
        opponent_roster_core="#YAJ",
    )

    assert result == [MATCH, MATCH]
    assert filter_matches(result, limit=1) == [MATCH]


def test_large_tool_results_require_narrowing():
    rows: list[MatchRow] = [dict(MATCH) for _ in range(MAX_TOOL_RESULT_ROWS + 1)]

    result = guard_rows(rows, ["opponent", "stage"])

    assert result == TruncatedResult(total=len(rows), available_filters=["opponent", "stage"])
