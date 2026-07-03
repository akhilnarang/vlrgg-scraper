import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from bs4 import BeautifulSoup

from app.services import events
from app.constants import EventStatus, EVENTS_URL
from app.exceptions import ScrapingError

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


def _build_paged_mock_get():
    page1_html = (FIXTURE_DIR / "events_page1.html").read_bytes()
    page2_html = (FIXTURE_DIR / "events_page2.html").read_bytes()
    empty_html = (FIXTURE_DIR / "events_empty.html").read_bytes()

    response_by_url = {
        # Page 1 is fetched without an explicit &page param by get_events.
        EVENTS_URL: page1_html,
        f"{EVENTS_URL}&page=2": page2_html,
    }

    async def mock_get(url: str, *args, **kwargs):
        # Any page beyond what we have a fixture for is treated as empty (out of range).
        content = response_by_url.get(url, empty_html)
        return _mock_response(url, content)

    return mock_get


@pytest.mark.asyncio
async def test_get_events():
    # Load the fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "events.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await events.get_events(mock_redis)

    # Assertions
    assert len(result) == 1

    # Check first event
    first_event = result[0]
    assert first_event.id == "2283"
    assert first_event.title == "Valorant Champions 2025"
    assert first_event.status == EventStatus.ONGOING
    assert first_event.prize == "$2,250,000"
    assert first_event.dates == "Sep 12—Oct 6"
    assert first_event.location == "fr"
    assert str(first_event.img) == "https://owcdn.net/img/63067806d167d.png"


@pytest.mark.asyncio
async def test_get_event_by_id():
    # Load the fixture HTML for event
    fixture_path = Path(__file__).parent / "fixtures" / "event_2283.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP responses
    mock_response_event = AsyncMock()
    mock_response_event.status_code = 200
    mock_response_event.content = html_content.encode("utf-8")

    # For matches, use a simple mock since we don't have a fixture
    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = b"<html><body></body></html>"

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_event, mock_response_matches]):
        result = await events.get_event_by_id("2283")

    # Assertions
    assert result.id == "2283"
    assert result.title == "Valorant Champions 2025"
    assert result.status == EventStatus.ONGOING
    assert result.prize == "$2,250,000 USD"
    assert result.dates == "Sep 12, 2025 - Oct 6, 2025"
    assert result.location == "Accor Arena, Paris"
    assert str(result.img) == "https://owcdn.net/img/63067806d167d.png"
    assert isinstance(result.matches, list)


@pytest.mark.asyncio
async def test_get_event_by_id_new_header_layout():
    """VLR.gg redesigned the event header (VLR-SCRAPER-8R): title moved from
    h1.wf-title to h1.event-header-main-title, subtitle to h2.event-header-main-desc,
    and dates/prize/location from flat div.event-desc-item-value siblings to
    label/value pairs under div.event-header-main-meta."""
    fixture_path = Path(__file__).parent / "fixtures" / "event_2863.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    mock_response_event = AsyncMock()
    mock_response_event.status_code = 200
    mock_response_event.content = html_content.encode("utf-8")

    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = b"<html><body></body></html>"

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_event, mock_response_matches]):
        result = await events.get_event_by_id("2863")

    assert result.id == "2863"
    assert result.title == "VCT 2026: EMEA Stage 1"
    assert result.subtitle == "Part of the Valorant Champions Tour, Riot's official 2026 tournament circuit."
    assert result.dates == "Apr 1 – May 18, 2026"
    assert result.prize == "TBD"
    assert result.location == "Riot Games Arena, Berlin"
    assert str(result.img) == "https://owcdn.net/img/65ab59620a233.png"
    assert isinstance(result.matches, list)


@pytest.mark.asyncio
async def test_get_event_by_id_region_meta_label():
    """Some events label the location slot "Region" instead of "Location", with an
    empty value resolved via the flag class (e.g. mod-fr -> "fr"). The metadata
    parser must accept either label and fall back to the flag."""
    fixture_path = Path(__file__).parent / "fixtures" / "event_2842.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    mock_response_event = AsyncMock()
    mock_response_event.status_code = 200
    mock_response_event.content = html_content.encode("utf-8")

    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = b"<html><body></body></html>"

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_event, mock_response_matches]):
        result = await events.get_event_by_id("2842")

    assert result.id == "2842"
    assert result.title == "Challengers 2026: France Revolution Stage 2"
    assert result.dates == "Apr 4 – May 13, 2026"
    assert result.prize == "€10,000 EUR~ $11,825"
    assert result.location == "fr"


@pytest.mark.asyncio
async def test_get_event_by_id_location_without_flag_does_not_crash():
    """An empty location value with no flag icon must degrade to "" rather than
    crash with AttributeError (the original VLR-SCRAPER-8R bug class)."""
    html = """
    <html><body>
      <div class="event-header">
        <h1 class="event-header-main-title">Some Event</h1>
        <h2 class="event-header-main-desc">A subtitle</h2>
        <div class="event-header-main-meta">
          <div><div class="label">Dates</div><div class="value">Jan 1 – Feb 1, 2026</div></div>
          <div><div class="label">Prize</div><div class="value">$1,000</div></div>
          <div><div class="label">Region</div><div class="value"></div></div>
        </div>
        <div class="event-header-thumb"><img src="//owcdn.net/img/x.png"/></div>
      </div>
      <div class="event-sidebar-matches"></div>
    </body></html>
    """

    mock_response_event = AsyncMock()
    mock_response_event.status_code = 200
    mock_response_event.content = html.encode("utf-8")

    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = b"<html><body></body></html>"

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_event, mock_response_matches]):
        result = await events.get_event_by_id("9999")

    assert result.id == "9999"
    assert result.location == ""
    assert result.dates == "Jan 1 – Feb 1, 2026"
    assert result.subtitle == "A subtitle"


@pytest.mark.asyncio
async def test_get_events_scraping_error():
    """Test that ScrapingError is raised when VLR.gg returns a bad response."""
    # Mock the HTTP response with bad status
    mock_response = AsyncMock()
    mock_response.status_code = 500

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(ScrapingError) as exc_info:
            await events.get_events(mock_redis)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "VLR.gg server returned an error"


def _build_event_card(status_class: str, status_text: str) -> BeautifulSoup:
    html = f"""
    <a class="wf-card mod-flex event-item" href="/event/9999/some-event">
      <div class="event-item-inner">
        <div class="event-item-title">Some Event</div>
        <div>
          <div class="event-item-desc-item">
            <span class="event-item-desc-item-status {status_class}">{status_text}</span>
          </div>
          <div class="event-item-desc-item mod-prize">$1,000</div>
          <div class="event-item-desc-item mod-dates">Jan 1—Feb 1</div>
          <div class="event-item-desc-item mod-location">
            <i class="flag mod-kr"></i>
          </div>
        </div>
        <div class="event-item-thumb">
          <img src="//owcdn.net/img/test.png"/>
        </div>
      </div>
    </a>
    """
    return BeautifulSoup(html, "lxml").find("a", class_="wf-card")


@pytest.mark.asyncio
async def test_parse_event_paused_status():
    event_tag = _build_event_card("mod-paused", "paused")
    mock_redis = AsyncMock()

    result = await events.parse_event(event_tag, mock_redis)

    assert result.status == EventStatus.PAUSED
    assert result.id == "9999"
    assert result.title == "Some Event"


@pytest.mark.asyncio
async def test_parse_event_unknown_status_falls_back(caplog):
    event_tag = _build_event_card("mod-suspended", "suspended")
    mock_redis = AsyncMock()

    import logging

    with caplog.at_level(logging.WARNING):
        result = await events.parse_event(event_tag, mock_redis)

    assert result.status == EventStatus.UNKNOWN
    assert any("Unknown VLR event status" in record.message for record in caplog.records)


def test_parse_event_standings_with_logo_first_column_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "event_2760.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, "lxml")
    standings_container = soup.find("div", class_="event-container")

    result = events.parse_event_standings(standings_container)

    assert len(result) >= 2
    assert result[0]["team"] == "Xi Lai Gaming"
    assert result[0]["country"] == "China"
    assert result[0]["wins"] == 0
    assert result[0]["losses"] == 0
    assert all(standing["team"] != "Spoiler hidden" for standing in result)


@pytest.mark.asyncio
async def test_get_events_default_single_page():
    mock_redis = AsyncMock()
    with patch("httpx.AsyncClient.get", side_effect=_build_paged_mock_get()):
        result = await events.get_events(mock_redis)

    # Default behaviour: only the first page of events.
    assert len(result) > 0
    # Parsing stayed intact across the real-page fixture.
    assert all(event.id for event in result)
    assert result[0].id == "2765"


@pytest.mark.asyncio
async def test_get_events_multi_page_returns_more_events():
    mock_redis = AsyncMock()
    with patch("httpx.AsyncClient.get", side_effect=_build_paged_mock_get()):
        default = await events.get_events(mock_redis)
        two_pages = await events.get_events(mock_redis, pages=2)

    # Two pages should yield more events than the default single page.
    assert len(two_pages) > len(default)
    assert len(two_pages) == len(default) + 50

    # Ordering is preserved: page 1 first, then page 2 appended after.
    assert [e.id for e in two_pages[: len(default)]] == [e.id for e in default]
    # No duplicates across the combined pages and parsing stayed intact.
    ids = [e.id for e in two_pages]
    assert len(set(ids)) == len(ids)
    assert all(e.id for e in two_pages)


@pytest.mark.asyncio
async def test_get_events_fetch_all_pages_stops_on_empty():
    # pages <= 0 means "fetch all"; only pages 1 and 2 have events here, the batch
    # walk should stop once it hits the empty out-of-range pages.
    mock_redis = AsyncMock()
    with patch("httpx.AsyncClient.get", side_effect=_build_paged_mock_get()):
        all_pages = await events.get_events(mock_redis, pages=0)
        two_pages = await events.get_events(mock_redis, pages=2)

    assert len(all_pages) == len(two_pages)


@pytest.mark.asyncio
async def test_get_events_later_page_error_raises_not_partial():
    """A non-200 on a later page must raise ScrapingError, never return a partial list."""
    page1_html = (FIXTURE_DIR / "events_page1.html").read_bytes()

    async def mock_get(url: str, *args, **kwargs):
        if url == EVENTS_URL:
            return _mock_response(url, page1_html)
        resp = _mock_response(url, b"")
        resp.status_code = 503
        return resp

    mock_redis = AsyncMock()
    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        with pytest.raises(ScrapingError):
            await events.get_events(mock_redis, pages=0)
