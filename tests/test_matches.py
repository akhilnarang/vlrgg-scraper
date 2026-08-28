from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import matches

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_match_details_follow_the_public_response_contract(http_response):
    response = http_response("https://www.vlr.gg/12345", (FIXTURE_DIR / "match_12345.html").read_bytes())

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await matches.match_by_id("12345", AsyncMock())

    assert [(team.name, team.score) for team in result.teams] == [("Team A", 2), ("Team B", 1)]
    assert result.event.id == "2283"
    assert result.event.series == "Event Series"
    assert result.map_count == 1
    assert [(item.map, [team.score for team in item.teams]) for item in result.data] == [("Lotus", [13, 10])]
    member = result.data[0].members[0]
    assert (member.id, member.name, member.team) == ("2114", "Kinguyen", "Team A")
    assert (member.agents[0].title, member.rating, member.kills) == ("Raze", 1.42, 29)


@pytest.mark.asyncio
async def test_match_list_keeps_each_upcoming_date_group(monkeypatch, http_response):
    responses = {
        constants.UPCOMING_MATCHES_URL: http_response(
            constants.UPCOMING_MATCHES_URL, (FIXTURE_DIR / "matches.html").read_bytes()
        ),
        constants.PAST_MATCHES_URL: http_response(
            constants.PAST_MATCHES_URL, (FIXTURE_DIR / "matches_results.html").read_bytes()
        ),
    }
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)

    with patch("httpx.AsyncClient.get", side_effect=lambda url, *_args, **_kwargs: responses[url]):
        result = await matches.match_list(AsyncMock())

    names = {match.id: (match.team1.name, match.team2.name) for match in result}
    assert names["684611"] == ("FULL SENSE", "FUT Esports")
    assert names["684612"] == ("LEVIATÁN", "Global Esports")
    assert names["684610"] == ("Team Vitality", "Dragon Ranger Gaming")


@pytest.mark.asyncio
async def test_completed_matches_clamp_pages_and_keep_results_in_order(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    pages = {
        constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes(),
        matches.completed_matches_url(2): (FIXTURE_DIR / "matches_results_page2.html").read_bytes(),
    }

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages)) as get:
        result = await matches.get_completed_matches(AsyncMock(), pages=9999)

    assert len(result) == 100
    assert len({match.id for match in result}) == 100
    assert result[0].id == "670476"
    assert result[50].id == "684615"
    assert get.call_count <= constants.MAX_PAGINATION_PAGES


@pytest.mark.asyncio
async def test_completed_matches_do_not_return_partial_results(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    pages = {constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes()}
    failures = {matches.completed_matches_url(2): 502}

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await matches.get_completed_matches(AsyncMock(), pages=2)
