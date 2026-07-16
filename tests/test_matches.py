from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import app.constants as constants
from app.constants import MAX_PAGINATION_PAGES
from app.services import matches


@pytest.mark.asyncio
async def test_get_team_data_parses_completed_match_score():
    """A completed match must report each team's score from the header."""
    html = (Path(__file__).parent / "fixtures" / "match_header_completed.html").read_text()
    header = BeautifulSoup(html, "lxml").find_all("div", class_="match-header")

    result = await matches.get_team_data(header, None)

    assert [team["name"] for team in result] == ["NRG", "FNATIC"]
    assert [team["score"] for team in result] == ["3", "2"]


@pytest.mark.asyncio
async def test_get_team_data_leaves_score_none_for_upcoming_match():
    """An upcoming match has no score element, so both scores stay None."""
    html = """
    <div class="match-header">
      <a class="match-header-link" href="/team/1/alpha"><img src="/img/a.png"/></a>
      <div class="wf-title-med">Alpha</div>
      <a class="match-header-link" href="/team/2/beta"><img src="/img/b.png"/></a>
      <div class="wf-title-med">Beta</div>
      <div class="match-header-vs-score">
        <div class="match-header-vs-note">upcoming</div>
      </div>
    </div>
    """
    header = BeautifulSoup(html, "lxml").find_all("div", class_="match-header")

    result = await matches.get_team_data(header, None)

    assert [team["score"] for team in result] == [None, None]


def test_parse_div_based_overview_scoreboard():
    html = """
    <div class="vm-stats-game">
      <div class="ovw-row mod-head"></div>
      <div class="ovw-row">
        <div class="ovw-cell mod-player">
          <div class="ovw-player">
            <a href="/player/2114/kinguyen">
              <div class="ovw-player-name">Kinguyen</div>
              <div class="ovw-player-tag">ky5</div>
            </a>
          </div>
          <div class="ovw-agents">
            <img src="/img/vlr/game/agents/raze.png" title="Raze">
          </div>
        </div>
        <div data-col="rating2"><span class="side mod-both">1.42</span></div>
        <div data-col="acs"><span class="side mod-both">346</span></div>
        <div data-col="kills"><span class="side mod-both">29</span></div>
        <div data-col="deaths"><span class="side mod-both">13</span></div>
        <div data-col="assists"><span class="side mod-both">3</span></div>
        <div data-col="kast"><span class="side mod-both">74%</span></div>
        <div data-col="adr"><span class="side mod-both">188</span></div>
        <div data-col="hsp"><span class="side mod-both">22%</span></div>
        <div data-col="fb"><span class="side mod-both">5</span></div>
        <div data-col="fd"><span class="side mod-both">3</span></div>
        <div data-col="fk-diff"><span class="side mod-both">+2</span></div>
      </div>
    </div>
    """
    scoreboard = BeautifulSoup(html, "lxml").select_one("div.vm-stats-game")

    result = matches.parse_overview_scoreboard(scoreboard, {"ky5": "keepYOURskill"})

    assert result == [
        {
            "id": "2114",
            "name": "Kinguyen",
            "team": "keepYOURskill",
            "agents": [{"title": "Raze", "img": "https://www.vlr.gg/img/vlr/game/agents/raze.png"}],
            "rating": 1.42,
            "acs": 346,
            "kills": 29,
            "deaths": 13,
            "assists": 3,
            "kast": 74,
            "adr": 188,
            "headshot_percent": 22,
            "first_kills": 5,
            "first_deaths": 3,
            "first_kills_diff": 2,
        }
    ]


@pytest.mark.asyncio
async def test_match_list_keeps_first_upcoming_date_group(monkeypatch):
    fixture_dir = Path(__file__).parent / "fixtures"
    upcoming_html = (fixture_dir / "matches.html").read_bytes()
    results_html = (fixture_dir / "matches_results.html").read_bytes()

    response_by_url = {
        constants.UPCOMING_MATCHES_URL: _mock_response(constants.UPCOMING_MATCHES_URL, upcoming_html),
        constants.PAST_MATCHES_URL: _mock_response(constants.PAST_MATCHES_URL, results_html),
    }

    async def mock_get(url: str, *args, **kwargs):
        return response_by_url[url]

    mock_redis = AsyncMock()
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await matches.match_list(mock_redis)

    result_by_id = {match.id: match for match in result}

    assert result_by_id["684611"].team1.name == "FULL SENSE"
    assert result_by_id["684611"].team2.name == "FUT Esports"
    assert result_by_id["684612"].team1.name == "LEVIATÁN"
    assert result_by_id["684612"].team2.name == "Global Esports"
    assert result_by_id["684610"].team1.name == "Team Vitality"
    assert result_by_id["684610"].team2.name == "Dragon Ranger Gaming"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


def _build_completed_mock_get():
    fixture_dir = Path(__file__).parent / "fixtures"
    page1_html = (fixture_dir / "matches_results_page1.html").read_bytes()
    page2_html = (fixture_dir / "matches_results_page2.html").read_bytes()
    empty_html = b"<html><body></body></html>"

    response_by_url = {
        # Page 1 is fetched without an explicit ?page param by get_completed_matches.
        constants.PAST_MATCHES_URL: page1_html,
        f"{constants.PAST_MATCHES_URL}?page=2": page2_html,
    }

    async def mock_get(url: str, *args, **kwargs):
        # Any page beyond what we have a fixture for is treated as empty (out of range).
        return _mock_response(url, response_by_url.get(url, empty_html))

    return mock_get


@pytest.mark.asyncio
async def test_completed_matches_default_single_page(monkeypatch):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", side_effect=_build_completed_mock_get()):
        result = await matches.get_completed_matches(mock_redis)

    # Default behaviour: only the first results page (50 cards).
    assert len(result) == 50
    assert all(match.id for match in result)


@pytest.mark.asyncio
async def test_completed_matches_multi_page_returns_more(monkeypatch):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", side_effect=_build_completed_mock_get()):
        default = await matches.get_completed_matches(mock_redis)
        two_pages = await matches.get_completed_matches(mock_redis, pages=2)

    # Two pages should yield more matches than the single-page default.
    assert len(two_pages) == 100
    assert len(two_pages) > len(default)

    # Ordering preserved: page 1 first, then page 2 appended after.
    assert [m.id for m in two_pages[:50]] == [m.id for m in default]
    # No duplicates across pages and parsing stayed intact.
    ids = [m.id for m in two_pages]
    assert len(set(ids)) == len(ids)
    assert all(m.id for m in two_pages)


@pytest.mark.asyncio
async def test_completed_matches_fetch_all_stops_on_empty(monkeypatch):
    # pages <= 0 means "fetch all"; only pages 1 and 2 have matches here,
    # the batch walk should stop once it hits the empty out-of-range pages.
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", side_effect=_build_completed_mock_get()):
        result = await matches.get_completed_matches(mock_redis, pages=0)

    assert len(result) == 100


@pytest.mark.asyncio
async def test_completed_matches_later_page_error_raises_not_partial(monkeypatch):
    """A non-200 on a later page must raise ScrapingError, never return a partial list."""
    from app.exceptions import ScrapingError

    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    page1_html = (Path(__file__).parent / "fixtures" / "matches_results_page1.html").read_bytes()

    async def mock_get(url: str, *args, **kwargs):
        if url == constants.PAST_MATCHES_URL:
            return _mock_response(url, page1_html)
        resp = _mock_response(url, b"")
        resp.status_code = 502
        return resp

    mock_redis = AsyncMock()
    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        with pytest.raises(ScrapingError):
            await matches.get_completed_matches(mock_redis, pages=0)


@pytest.mark.asyncio
async def test_match_by_id():
    # Load the fixture HTML for match
    fixture_path = Path(__file__).parent / "fixtures" / "match_12345.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await matches.match_by_id("12345", mock_redis)

    # Assertions — verify all fields parsed via find() (Phase 6 conversion)
    assert result.teams is not None
    assert isinstance(result.teams, list)
    # Assert the values, not just the shape: `teams is not None` passes on a list
    # of nulls, which is exactly how the `js-spoiler` -> `sp-hide` rename shipped.
    assert [team.name for team in result.teams] == ["Team A", "Team B"]
    assert [team.score for team in result.teams] == [2, 1]

    # Event data (get_event_data uses find() for series, stage, img)
    assert result.event is not None
    assert result.event.id != ""
    assert result.event.series != ""
    assert result.event.stage != ""
    assert result.event.img is not None

    # Videos
    assert result.videos is not None
    assert isinstance(result.videos.streams, list)
    assert isinstance(result.videos.vods, list)

    # Map data (get_map_data uses find() for round_number; parse_scoreboard uses find() for player data)
    if result.data:
        for map_data in result.data:
            assert map_data.map != ""
            assert len(map_data.teams) == 2
            for member in map_data.members:
                assert member.name != ""
                assert member.team != ""
                assert isinstance(member.agents, list)
            for round_info in map_data.rounds:
                assert isinstance(round_info.round_number, int)


@pytest.mark.asyncio
async def test_completed_matches_page_cap(monkeypatch):
    """Requesting pages=9999 (bounded mode) must issue at most MAX_PAGINATION_PAGES HTTP fetches."""
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    mock_redis = AsyncMock()

    fixture_dir = Path(__file__).parent / "fixtures"
    page1_html = (fixture_dir / "matches_results_page1.html").read_bytes()

    call_count = 0

    async def counting_mock_get(url: str, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = AsyncMock()
        r.status_code = 200
        r.content = page1_html  # always non-empty; empty-stop never triggers
        r.url = url
        return r

    with patch("httpx.AsyncClient.get", side_effect=counting_mock_get):
        result = await matches.get_completed_matches(mock_redis, pages=9999)

    # Total HTTP requests must not exceed the hard cap.
    assert call_count <= MAX_PAGINATION_PAGES
    # Dedup must have removed repeated ids; only unique matches survive.
    ids = [m.id for m in result]
    assert len(set(ids)) == len(ids)


@pytest.mark.asyncio
async def test_completed_matches_raw_card_count_stop(monkeypatch):
    """A page whose raw match cards all fail to parse (wf-module-item anchors present but no
    preceding wf-label sibling) must NOT trigger an early stop — the crawl should continue
    to the next page and stop only when the raw card count genuinely reaches zero."""
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    mock_redis = AsyncMock()

    fixture_dir = Path(__file__).parent / "fixtures"
    page1_html = (fixture_dir / "matches_results_page1.html").read_bytes()

    # A page that has wf-module-item anchors inside wf-card divs, but no wf-label sibling →
    # count_result_cards > 0 but parse_matches returns [] (raw_matches list is empty).
    page_with_cards_no_label = (
        b"<html><body>"
        b'<div class="wf-card"><a class="wf-module-item" href="/777777/vs-a-b">'
        b'<div class="match-item-time">12:00 PM</div>'
        b'<div class="ml-status">completed</div>'
        b"</a></div>"
        b"</body></html>"
    )
    empty_html = b"<html><body></body></html>"

    requested_urls: list[str] = []

    async def mock_get(url: str, *args, **kwargs):
        requested_urls.append(url)
        r = AsyncMock()
        r.status_code = 200
        r.url = url
        if url == constants.PAST_MATCHES_URL:
            r.content = page1_html
        elif url == matches.completed_matches_url(2):
            # Raw cards exist but parse_matches returns [] (no wf-label) → NOT a true empty page.
            r.content = page_with_cards_no_label
        else:
            # Page 3+ are truly empty (no raw cards).
            r.content = empty_html
        return r

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await matches.get_completed_matches(mock_redis, pages=0)

    # Under the raw-card-count guard, page 2 must NOT have triggered an early stop —
    # the crawl must have fetched page 3 before stopping on the truly empty page.
    assert matches.completed_matches_url(3) in requested_urls
    # Only page 1's matches are returned (page 2 had cards but nothing parseable).
    assert len(result) == 50
