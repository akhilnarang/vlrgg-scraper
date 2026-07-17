from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.constants as constants
from app.constants import MAX_PAGINATION_PAGES
from app.services import team

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TEAM_ID = "624"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


def _build_mock_get():
    team_html = (FIXTURE_DIR / "team_624.html").read_bytes()
    upcoming_html = (FIXTURE_DIR / "team_624_upcoming.html").read_bytes()
    page1_html = (FIXTURE_DIR / "team_624_completed_page1.html").read_bytes()
    page2_html = (FIXTURE_DIR / "team_624_completed_page2.html").read_bytes()
    empty_html = (FIXTURE_DIR / "team_624_completed_empty.html").read_bytes()

    base_completed = constants.TEAM_COMPLETED_MATCHES_URL.format(TEAM_ID)
    response_by_url = {
        constants.TEAM_URL.format(TEAM_ID): team_html,
        constants.TEAM_UPCOMING_MATCHES_URL.format(TEAM_ID): upcoming_html,
        # Page 1 is fetched without an explicit &page param by get_team_data.
        base_completed: page1_html,
        f"{base_completed}&page=2": page2_html,
    }

    async def mock_get(url: str, *args, **kwargs):
        # Any page beyond what we have a fixture for is treated as empty (out of range).
        content = response_by_url.get(url, empty_html)
        return _mock_response(url, content)

    return mock_get


@pytest.mark.asyncio
async def test_team_default_single_page():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await team.get_team_data(TEAM_ID)

    assert result.name == "Paper Rex"
    # Default behaviour: only the first page of completed matches (50).
    assert len(result.completed) == 50
    # Roster-"core" tags are captured for both sides (e.g. "#ACM" vs "#YAJ").
    first = result.completed[0]
    assert first.roster_core and first.roster_core.startswith("#")
    assert first.opponent_roster_core and first.opponent_roster_core.startswith("#")


@pytest.mark.asyncio
async def test_team_social_links_are_classified():
    """Twitter moved to x.com, and only the real site belongs in `website`.

    The team header lists site, X and Facebook links. Matching "twitter.com"
    alone misses the x.com link, and letting every unmatched link fall through to
    `website` leaves whichever social happens to come last.
    """
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await team.get_team_data(TEAM_ID)

    assert result.twitter == "https://x.com/pprxteam"
    assert str(result.website) == "https://pprx.team/"


@pytest.mark.asyncio
async def test_team_multi_page_returns_more_matches():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        default = await team.get_team_data(TEAM_ID)
        two_pages = await team.get_team_data(TEAM_ID, completed_pages=2)

    # Two pages should yield roughly twice as many completed matches as the default.
    assert len(two_pages.completed) == 100
    assert len(two_pages.completed) > len(default.completed)

    # Ordering is preserved: page 1 first, then page 2 appended after.
    assert [m.id for m in two_pages.completed[:50]] == [m.id for m in default.completed]
    # No duplicates across the combined pages and parsing stayed intact.
    ids = [m.id for m in two_pages.completed]
    assert len(set(ids)) == len(ids)
    assert all(m.id for m in two_pages.completed)


@pytest.mark.asyncio
async def test_team_fetch_all_pages_stops_on_empty():
    # completed_pages <= 0 means "fetch all"; only pages 1 and 2 have matches here,
    # the batch walk should stop once it hits the empty out-of-range pages.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await team.get_team_data(TEAM_ID, completed_pages=0)

    assert len(result.completed) == 100


@pytest.mark.asyncio
async def test_team_dedup_stop_on_repeated_page():
    """Full-history mode (completed_pages=0) must terminate — and not hang — when every
    additional page returns only ids already seen, and the result must contain no duplicates."""
    team_html = (FIXTURE_DIR / "team_624.html").read_bytes()
    upcoming_html = (FIXTURE_DIR / "team_624_upcoming.html").read_bytes()
    page1_html = (FIXTURE_DIR / "team_624_completed_page1.html").read_bytes()

    async def repeated_page_mock_get(url: str, *args, **kwargs):
        if url == constants.TEAM_URL.format(TEAM_ID):
            content = team_html
        elif url == constants.TEAM_UPCOMING_MATCHES_URL.format(TEAM_ID):
            content = upcoming_html
        else:
            # The completed-match URL for page 1 AND every higher page returns the same content.
            content = page1_html
        r = AsyncMock()
        r.status_code = 200
        r.content = content
        r.url = url
        return r

    with patch("httpx.AsyncClient.get", side_effect=repeated_page_mock_get):
        result = await team.get_team_data(TEAM_ID, completed_pages=0)

    # The dedup guard must have detected zero new ids on the first repeated page and stopped.
    assert len(result.completed) == 50  # only page 1 items
    ids = [m.id for m in result.completed]
    assert len(set(ids)) == len(ids)  # no duplicates


@pytest.mark.asyncio
async def test_team_completed_pages_cap():
    """Requesting completed_pages=9999 (bounded mode) must clamp to MAX_PAGINATION_PAGES."""
    team_html = (FIXTURE_DIR / "team_624.html").read_bytes()
    upcoming_html = (FIXTURE_DIR / "team_624_upcoming.html").read_bytes()
    page1_html = (FIXTURE_DIR / "team_624_completed_page1.html").read_bytes()

    call_count = 0

    async def counting_mock_get(url: str, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if url == constants.TEAM_URL.format(TEAM_ID):
            content = team_html
        elif url == constants.TEAM_UPCOMING_MATCHES_URL.format(TEAM_ID):
            content = upcoming_html
        else:
            content = page1_html  # always non-empty, so empty-stop never triggers
        r = AsyncMock()
        r.status_code = 200
        r.content = content
        r.url = url
        return r

    with patch("httpx.AsyncClient.get", side_effect=counting_mock_get):
        result = await team.get_team_data(TEAM_ID, completed_pages=9999)

    # The 3 team/upcoming/completed-page-1 requests plus at most MAX-1 extra pages.
    # Total HTTP calls must not exceed 3 (fixed) + MAX_PAGINATION_PAGES.
    assert call_count <= 3 + MAX_PAGINATION_PAGES
    # Result must contain no duplicate ids.
    ids = [m.id for m in result.completed]
    assert len(set(ids)) == len(ids)


@pytest.mark.asyncio
async def test_team_cache_hit_skips_fetch():
    """A cache hit returns the stored Team and makes no vlr.gg request."""
    # Build a real serialized Team to return from the fake cache.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        seed = await team.get_team_data(TEAM_ID)
    cached_json = seed.model_dump_json()

    http_get = AsyncMock(side_effect=AssertionError("must not fetch on cache hit"))
    with (
        patch("app.services.team.cache.get", new=AsyncMock(return_value=cached_json)),
        patch("app.services.team.cache.set", new=AsyncMock()) as cset,
        patch("httpx.AsyncClient.get", new=http_get),
    ):
        result = await team.get_team_data(TEAM_ID)

    assert result.name == seed.name
    http_get.assert_not_called()
    cset.assert_not_called()


@pytest.mark.asyncio
async def test_team_cache_miss_fetches_and_stores():
    """A cache miss fetches live and writes the result back under team:{id}:{pages}."""
    with (
        patch("app.services.team.cache.get", new=AsyncMock(return_value=None)),
        patch("app.services.team.cache.set", new=AsyncMock()) as cset,
        patch("httpx.AsyncClient.get", side_effect=_build_mock_get()),
    ):
        result = await team.get_team_data(TEAM_ID, completed_pages=2)

    cset.assert_awaited_once()
    assert cset.await_args.args[0] == f"team:{TEAM_ID}:2"
    assert cset.await_args.kwargs["ttl"] == constants.CACHE_TTL_TEAM
    assert result.name == "Paper Rex"


@pytest.mark.asyncio
async def test_team_later_page_error_raises_and_is_not_cached():
    """A non-200 on a later completed-matches page must raise (not return/cache a partial)."""
    from app.exceptions import ScrapingError

    team_html = (FIXTURE_DIR / "team_624.html").read_bytes()
    upcoming_html = (FIXTURE_DIR / "team_624_upcoming.html").read_bytes()
    page1_html = (FIXTURE_DIR / "team_624_completed_page1.html").read_bytes()
    base_completed = constants.TEAM_COMPLETED_MATCHES_URL.format(TEAM_ID)

    async def mock_get(url: str, *args, **kwargs):
        r = AsyncMock()
        r.url = url
        if url == constants.TEAM_URL.format(TEAM_ID):
            r.status_code, r.content = 200, team_html
        elif url == constants.TEAM_UPCOMING_MATCHES_URL.format(TEAM_ID):
            r.status_code, r.content = 200, upcoming_html
        elif url == base_completed:  # page 1 OK
            r.status_code, r.content = 200, page1_html
        else:  # page 2+ transiently fails
            r.status_code, r.content = 503, b""
        return r

    with (
        patch("app.services.team.cache.get", new=AsyncMock(return_value=None)),
        patch("app.services.team.cache.set", new=AsyncMock()) as cset,
        patch("httpx.AsyncClient.get", side_effect=mock_get),
    ):
        with pytest.raises(ScrapingError):
            await team.get_team_data(TEAM_ID, completed_pages=0)

    cset.assert_not_called()  # partial history must never be cached
