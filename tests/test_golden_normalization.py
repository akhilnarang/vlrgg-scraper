from enum import Enum

import pytest
from pydantic import BaseModel

from tests.golden.test_live_golden import (
    GoldenValue,
    TimestampKey,
    normalize_timestamp,
    normalize_volatile_fields,
    serialize,
)


class ExampleStatus(str, Enum):
    READY = "ready"


class ExampleModel(BaseModel):
    name: str


def test_normalize_volatile_fields_preserves_shape() -> None:
    """Timestamp normalization must retain keys and meaningful null values."""
    value: GoldenValue = {
        "date": "2026-07-15T03:00:00Z",
        "time": "03:00:00",
        "eta": "in 2 hours",
        "nested": [{"date": None, "name": "Example"}],
    }

    assert normalize_volatile_fields(value) == {
        "date": "<date>",
        "time": "<time>",
        "nested": [{"date": None, "name": "Example"}],
    }


@pytest.mark.parametrize("value", [None, True, 7, 2.5, "text", [], {}])
def test_normalize_volatile_fields_preserves_nonvolatile_roots(value: GoldenValue) -> None:
    """Scalar values and empty containers must pass through unchanged."""
    assert normalize_volatile_fields(value) == value


def test_normalize_volatile_fields_removes_nested_eta() -> None:
    """Relative ETAs are omitted at every nesting depth."""
    value: GoldenValue = [{"name": "Example", "details": {"eta": "in 2 hours"}}]

    assert normalize_volatile_fields(value) == [{"name": "Example", "details": {}}]


def test_serialize_handles_supported_container_and_object_types() -> None:
    """Serialization must produce a JSON-shaped value for every supported branch."""
    value = {
        7: (ExampleModel(name="Example"), ExampleStatus.READY),
        "items": [ExampleModel(name="Nested")],
    }

    assert serialize(value) == {
        "7": [{"name": "Example"}, "ready"],
        "items": [{"name": "Nested"}],
    }


def test_different_timezone_values_normalize_identically() -> None:
    """Equivalent response shapes must not depend on the request location."""
    india: GoldenValue = {"date": "2026-07-15T18:30:00+05:30", "time": "18:30:00", "eta": "soon"}
    utc: GoldenValue = {"date": "2026-07-15T13:00:00Z", "time": "13:00:00", "eta": "later"}

    assert normalize_volatile_fields(india) == normalize_volatile_fields(utc) == {
        "date": "<date>",
        "time": "<time>",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [("date", "not-a-date"), ("time", "25:99:00"), ("date", 123)],
)
def test_normalize_timestamp_rejects_malformed_values(key: TimestampKey, value: GoldenValue) -> None:
    """Normalization must not conceal malformed scraper output."""
    with pytest.raises(ValueError):
        normalize_timestamp(key, value)


@pytest.mark.parametrize("value", ["TBD", "TBD*"])
def test_normalize_timestamp_accepts_tbd_time(value: str) -> None:
    """VLR's plain and decorated TBD markers are valid non-ISO match times."""
    assert normalize_timestamp("time", value) == "<time>"
