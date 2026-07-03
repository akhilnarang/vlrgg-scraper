from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.constants as constants
from app import schemas
from app.services import player
from app.constants import MAX_PAGINATION_PAGES

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PLAYER_ID = "45"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


def _build_mock_get():
    overview_html = (FIXTURE_DIR / "player_45.html").read_bytes()
    page1_html = (FIXTURE_DIR / "player_matches_45_page1.html").read_bytes()
    page2_html = (FIXTURE_DIR / "player_matches_45_page2.html").read_bytes()
    page3_html = (FIXTURE_DIR / "player_matches_45_page3.html").read_bytes()
    page4_html = (FIXTURE_DIR / "player_matches_45_page4.html").read_bytes()
    empty_html = (FIXTURE_DIR / "player_matches_45_empty.html").read_bytes()

    response_by_url = {
        constants.PLAYER_URL.format(PLAYER_ID): overview_html,
        player.player_matches_url(PLAYER_ID, 1): page1_html,
        player.player_matches_url(PLAYER_ID, 2): page2_html,
        player.player_matches_url(PLAYER_ID, 3): page3_html,
        player.player_matches_url(PLAYER_ID, 4): page4_html,
    }

    async def mock_get(url: str, *args, **kwargs):
        # Any page beyond what we have a fixture for is treated as empty (out of range).
        content = response_by_url.get(url, empty_html)
        return _mock_response(url, content)

    return mock_get


@pytest.mark.asyncio
async def test_player_matches_default_single_page():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await player.get_player_matches(PLAYER_ID)

    # Default behaviour: only the first page of matches (50).
    assert len(result) == 50
    # Dates parse to datetimes and teams are populated.
    assert all(isinstance(m.date, datetime) for m in result)
    assert all(m.team for m in result)
    assert all(m.opponent for m in result)
    # Roster-"core" tags captured for both sides (e.g. "#Q10K").
    assert result[0].roster_core and result[0].roster_core.startswith("#")
    assert result[0].opponent_roster_core and result[0].opponent_roster_core.startswith("#")


@pytest.mark.asyncio
async def test_player_matches_multi_page_returns_more():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        default = await player.get_player_matches(PLAYER_ID)
        two_pages = await player.get_player_matches(PLAYER_ID, pages=2)

    # Two pages should yield roughly twice as many matches as the default.
    assert len(two_pages) == 100
    assert len(two_pages) > len(default)

    # Ordering (newest first) is preserved: page 1 first, then page 2 appended after.
    assert [m.id for m in two_pages[:50]] == [m.id for m in default]
    # No duplicates across the combined pages and parsing stayed intact.
    ids = [m.id for m in two_pages]
    assert len(set(ids)) == len(ids)
    assert all(m.id for m in two_pages)


@pytest.mark.asyncio
async def test_player_matches_fetch_all_pages_stops_on_empty():
    # pages <= 0 means "fetch all"; pages 1-4 have matches here (50 + 50 + 50 + 8),
    # the batch walk should stop once it hits the empty out-of-range pages.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await player.get_player_matches(PLAYER_ID, pages=0)

    assert len(result) == 158


@pytest.mark.asyncio
async def test_get_player_data_populates_matches():
    # Default match_pages=1 should fold the first page (50) of match history into the
    # player overview while preserving the existing player fields.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await player.get_player_data(PLAYER_ID)

    assert isinstance(result, schemas.Player)
    assert result.alias
    assert len(result.matches) == 50
    assert all(isinstance(m, schemas.PlayerMatch) for m in result.matches)
    assert all(isinstance(m.date, datetime) for m in result.matches)


@pytest.mark.asyncio
async def test_get_player_data_full_match_history():
    # match_pages=0 means "fetch all" pages of match history.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await player.get_player_data(PLAYER_ID, match_pages=0)

    assert len(result.matches) == 158


@pytest.mark.asyncio
async def test_player_matches_page_cap():
    """Requesting pages=9999 (bounded mode) must issue at most MAX_PAGINATION_PAGES HTTP fetches."""
    page1_html = (FIXTURE_DIR / "player_matches_45_page1.html").read_bytes()

    call_count = 0

    async def counting_mock_get(url: str, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = AsyncMock()
        r.status_code = 200
        r.content = page1_html  # always return a non-empty page so empty-stop doesn't kick in
        r.url = url
        return r

    with patch("httpx.AsyncClient.get", side_effect=counting_mock_get):
        result = await player.get_player_matches(PLAYER_ID, pages=9999)

    # Total HTTP requests must not exceed the hard cap.
    assert call_count <= MAX_PAGINATION_PAGES
    # Dedup must have removed the repeated pages; only page 1's unique ids survive.
    ids = [m.id for m in result]
    assert len(set(ids)) == len(ids)


@pytest.mark.asyncio
async def test_player_matches_dedup_stop_on_repeated_page():
    """Full-history mode (pages=0) must terminate when every additional page returns only
    ids already seen, and the returned list must contain no duplicate ids."""
    page1_html = (FIXTURE_DIR / "player_matches_45_page1.html").read_bytes()

    async def repeated_page_mock_get(url: str, *args, **kwargs):
        # Every URL — including page 2, 3, … — returns the same non-empty content.
        r = AsyncMock()
        r.status_code = 200
        r.content = page1_html
        r.url = url
        return r

    with patch("httpx.AsyncClient.get", side_effect=repeated_page_mock_get):
        result = await player.get_player_matches(PLAYER_ID, pages=0)

    # Crawl must have stopped after detecting zero new ids on the first repeated page.
    assert len(result) == 50  # only page 1's matches
    ids = [m.id for m in result]
    assert len(set(ids)) == len(ids)  # no duplicates


@pytest.mark.asyncio
async def test_player_data_cache_hit_skips_fetch():
    """A cache hit returns the stored Player and makes no vlr.gg request."""
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        seed = await player.get_player_data(PLAYER_ID)
    cached_json = seed.model_dump_json()

    http_get = AsyncMock(side_effect=AssertionError("must not fetch on cache hit"))
    with patch("app.services.player.cache.get", new=AsyncMock(return_value=cached_json)), \
         patch("app.services.player.cache.set", new=AsyncMock()) as cset, \
         patch("httpx.AsyncClient.get", new=http_get):
        result = await player.get_player_data(PLAYER_ID)

    assert result.alias == seed.alias
    http_get.assert_not_called()
    cset.assert_not_called()


@pytest.mark.asyncio
async def test_player_data_cache_miss_fetches_and_stores():
    """A cache miss fetches live and writes the result back under player:{id}:{pages}."""
    with patch("app.services.player.cache.get", new=AsyncMock(return_value=None)), \
         patch("app.services.player.cache.set", new=AsyncMock()) as cset, \
         patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        await player.get_player_data(PLAYER_ID, match_pages=1)

    cset.assert_awaited_once()
    assert cset.await_args.args[0] == f"player:{PLAYER_ID}:1"
    assert cset.await_args.kwargs["ttl"] == constants.CACHE_TTL_PLAYER
