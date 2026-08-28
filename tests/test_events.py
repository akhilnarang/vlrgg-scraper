from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.constants import EVENTS_URL
from app.exceptions import ScrapingError
from app.services import events

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_event_list_returns_full_history_in_order(http_get):
    pages = {
        EVENTS_URL: (FIXTURE_DIR / "events_page1.html").read_bytes(),
        events.events_url(2): (FIXTURE_DIR / "events_page2.html").read_bytes(),
    }
    with patch(
        "httpx.AsyncClient.get",
        side_effect=http_get(pages, fallback=(FIXTURE_DIR / "events_empty.html").read_bytes()),
    ):
        result = await events.get_events(AsyncMock(), pages=0)

    assert len(result) > 50
    assert len({event.id for event in result}) == len(result)
    assert result[0].id == "2765"


@pytest.mark.asyncio
async def test_event_list_does_not_return_partial_results(http_get):
    pages = {EVENTS_URL: (FIXTURE_DIR / "events_page1.html").read_bytes()}
    failures = {events.events_url(2): 503}

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await events.get_events(AsyncMock(), pages=2)


@pytest.mark.asyncio
async def test_event_details_follow_the_current_public_contract(http_response):
    event_response = http_response(
        "https://www.vlr.gg/event/2863",
        (FIXTURE_DIR / "event_2863.html").read_bytes(),
    )
    matches_response = http_response("https://www.vlr.gg/event/matches/2863", b"<html><body></body></html>")

    with patch("httpx.AsyncClient.get", side_effect=[event_response, matches_response]):
        result = await events.get_event_by_id("2863")

    assert result.id == "2863"
    assert result.title == "VCT 2026: EMEA Stage 1"
    assert result.subtitle == "Part of the Valorant Champions Tour, Riot's official 2026 tournament circuit."
    assert result.dates == "Apr 1 – May 18, 2026"
    assert result.prize == "TBD"
    assert result.location == "Riot Games Arena, Berlin"
    first_prize = result.prizes[0]
    assert first_prize.team is not None
    assert (first_prize.position, first_prize.team.name) == ("1st", "Team Heretics")
    assert (result.teams[0].name, result.teams[0].seed) == ("FUT Esports", "Alpha #1")
    assert result.standings == []
