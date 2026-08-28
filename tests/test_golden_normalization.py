import pytest
from pydantic import BaseModel

from tests.golden.test_live_golden import normalize_volatile_fields, serialize


class Snapshot(BaseModel):
    date: str
    dates: str
    time: str
    eta: str
    members: list[dict]


def test_golden_normalization_preserves_the_response_contract():
    snapshot = Snapshot(
        date="2026-07-15T03:00:00Z",
        dates="Apr 1 – May 18, 2026",
        time="TBD",
        eta="in 2 hours",
        members=[{"id": "43029", "name": "Onyx", "team": "PARON", "agents": [{"title": "Killjoy"}]}],
    )

    assert normalize_volatile_fields(serialize(snapshot)) == {
        "date": "<date>",
        "dates": "<dates>",
        "time": "<time>",
        "members": [{"id": "43029", "name": "<player-name>", "team": "PARON", "agents": [{"title": "Killjoy"}]}],
    }


def test_golden_normalization_rejects_malformed_parser_output():
    with pytest.raises(ValueError, match="invalid ISO date"):
        normalize_volatile_fields({"date": "not-a-date"})
