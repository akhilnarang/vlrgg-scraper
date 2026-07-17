import json
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from enum import Enum
from typing import Any, Literal, cast

import pytest
from pydantic import BaseModel
from pytest_regressions.data_regression import DataRegressionFixture

from app.services import events, matches, news, standings


pytestmark = pytest.mark.live_golden

# Only *frozen* entities belong here: completed matches, completed events,
# historical standings, and old articles, whose data never changes. For those,
# exact-equality snapshots are a stronger check than invariants, and any drift is
# a real parser break -- that is how the `js-spoiler` -> `sp-hide` rename was
# caught, when a match completed in 2025 started reporting score=null.
#
# The bar for adding an event here is that its *end date has passed*, not that it
# currently reports `status: completed`. Status is derived from counting sidebar
# headings (app/services/events.py), so an event whose upcoming section happens to
# be empty reads as completed while a late playoff match can still appear, flip it
# back to ongoing, and fail the snapshot as a false alarm. Event 2974 (ends
# Jul 26, 2026) was excluded for exactly that reason.
#
# Windowed endpoints (team, player, rankings, match list) are deliberately absent:
# their pages always show "now", so no baseline is stable at any data age. They
# get structural-health checks instead -- see `tests/live/test_parser_health.py`.
#
# NEVER bulk-regenerate these baselines. Regen per-file and read the diff: the
# diff *is* the alert. A wholesale `--force-regen` is what previously recorded a
# parser regression as the expected answer.


type GoldenValue = None | bool | int | float | str | list[GoldenValue] | dict[str, GoldenValue]
type TimestampKey = Literal["date", "time"]


def serialize(value: Any) -> GoldenValue:
    """Convert scraper results into the JSON-shaped values stored by golden tests."""
    if isinstance(value, BaseModel):
        return cast(GoldenValue, json.loads(value.model_dump_json()))
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, Enum):
        return cast(GoldenValue, value.value)
    return cast(GoldenValue, value)


def normalize_timestamp(key: TimestampKey, value: GoldenValue) -> GoldenValue:
    """Validate a serialized timestamp and replace its location-dependent value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null, got {type(value).__name__}")
    if key == "time" and "tbd" in value.lower():
        return "<time>"

    parsers = (datetime.fromisoformat,) if key == "date" else (datetime.fromisoformat, time.fromisoformat)
    iso_value = value.replace("Z", "+00:00")
    for parser in parsers:
        try:
            parser(iso_value)
        except ValueError:
            continue
        return f"<{key}>"
    raise ValueError(f"invalid ISO {key}: {value!r}")


def normalize_volatile_fields(value: GoldenValue) -> GoldenValue:
    """Stabilize location-dependent timestamps while preserving response shape."""
    if isinstance(value, list):
        return [normalize_volatile_fields(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, GoldenValue] = {}
        for key, item in value.items():
            if key == "eta":
                continue
            if key in {"date", "time"}:
                normalized[key] = normalize_timestamp(cast(TimestampKey, key), item)
            else:
                normalized[key] = normalize_volatile_fields(item)
        return normalized
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "loader"),
    [
        ("event_2283", lambda: events.get_event_by_id("2283", client=None)),
        ("event_2760", lambda: events.get_event_by_id("2760", client=None)),
        ("event_2842", lambda: events.get_event_by_id("2842", client=None)),
        ("event_2863", lambda: events.get_event_by_id("2863", client=None)),
        ("match_12345", lambda: matches.match_by_id("12345", redis_client=None)),
        ("match_673178", lambda: matches.match_by_id("673178", redis_client=None)),
        ("match_706763", lambda: matches.match_by_id("706763", redis_client=None)),
        ("match_542272", lambda: matches.match_by_id("542272", redis_client=None)),
        ("standings_2021", lambda: standings.standings_list(2021)),
        ("news_562934", lambda: news.news_by_id("562934")),
        ("news_562952", lambda: news.news_by_id("562952")),
    ],
)
async def test_live_vlr_parser_golden(
    case_name: str,
    loader: Callable[[], Awaitable[Any]],
    data_regression: DataRegressionFixture,
) -> None:
    """Compare a live scraper response with its location-independent baseline."""
    parsed = await loader()
    normalized = normalize_volatile_fields(serialize(parsed))
    assert isinstance(normalized, dict)
    data_regression.check(normalized, basename=case_name)
