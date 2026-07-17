from app.agent.filters import filter_matches, guard_rows, MAX_TOOL_RESULT_ROWS, TruncatedResult

MATCHES = [
    {
        "opponent": "Team Vitality",
        "event": "Masters London 2026",
        "stage": "Playoffs ⋅GF",
        "score": "2:1",
        "date": "2026-06-10T17:00:00+00:00",
        "roster_core": "#ACM",
        "opponent_roster_core": "#YAJ",
    },
    {
        "opponent": "LOUD",
        "event": "VCT 26: AMER",
        "stage": "Group Stage ⋅W1",
        "score": "0:2",
        "date": "2025-02-01T17:00:00+00:00",
        "roster_core": "#OLD",
        "opponent_roster_core": "#Z5K",
    },
]


def test_filter_by_opponent_substring_case_insensitive():
    out = filter_matches(MATCHES, opponent="vitality")
    assert len(out) == 1 and out[0]["opponent"] == "Team Vitality"


def test_filter_by_stage_and_limit():
    out = filter_matches(MATCHES, stage="GF", limit=5)
    assert len(out) == 1 and out[0]["stage"].endswith("GF")


def test_filter_by_date_range():
    out = filter_matches(MATCHES, date_from="2026-01-01")
    assert len(out) == 1 and out[0]["event"].startswith("Masters London")


def test_filter_by_roster_core():
    out = filter_matches(MATCHES, roster_core="#ACM")
    assert len(out) == 1 and out[0]["roster_core"] == "#ACM"


def test_filter_by_opponent_roster_core():
    out = filter_matches(MATCHES, opponent_roster_core="#Z5K")
    assert len(out) == 1 and out[0]["opponent"] == "LOUD"


def test_guard_passes_small_lists():
    assert guard_rows(MATCHES, ["opponent"]) == MATCHES


def test_guard_truncates_large_lists():
    big = [dict(MATCHES[0]) for _ in range(MAX_TOOL_RESULT_ROWS + 1)]
    out = guard_rows(big, ["opponent", "stage"])
    assert isinstance(out, TruncatedResult)
    assert out.truncated is True
    assert out.total == MAX_TOOL_RESULT_ROWS + 1
    assert "opponent" in out.available_filters
