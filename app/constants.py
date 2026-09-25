from enum import StrEnum

PREFIX = "https://www.vlr.gg"

EVENTS_URL = f"{PREFIX}/events/?tier=all"

EVENT_URL_WITH_ID = f"{PREFIX}/event/{{}}"

EVENT_URL_WITH_ID_MATCHES = f"{PREFIX}/event/matches/{{}}/?series_id=all"

MATCH_URL_WITH_ID = f"{PREFIX}/{{}}"

UPCOMING_MATCHES_URL = f"{PREFIX}/matches"

PAST_MATCHES_URL = f"{PREFIX}/matches/results"

NEWS_URL = f"{PREFIX}/news"

NEWS_URL_WITH_ID = f"{PREFIX}/{{}}"

TEAM_URL = f"{PREFIX}/team/{{}}"

TEAM_UPCOMING_MATCHES_URL = f"{PREFIX}/team/matches/{{}}/?group=upcoming"

TEAM_COMPLETED_MATCHES_URL = f"{PREFIX}/team/matches/{{}}/?group=completed"

PLAYER_URL = f"{PREFIX}/player/{{}}/?timespan=all"

PLAYER_MATCHES_URL = f"{PREFIX}/player/matches/{{}}"

RANKINGS_URL = f"{PREFIX}/rankings"

RANKING_URL_REGION = f"{PREFIX}{{}}"

SEARCH_URL = f"{PREFIX}/search?q={{}}&type={{}}"

STANDINGS_URL = f"{PREFIX}/vct-{{}}/standings"

TBD = "tbd"
TEST_MATCH_ID = "3141592653"
TEST_TICK_KEY = "vlrgg:push:test_tick"
# A tracked match whose page fails this many cron runs in a row is ended (e.g. VLR blocked our IP).
PUSH_FETCH_FAILURE_LIMIT = 3
PUSH_FETCH_FAILURES_KEY = "vlrgg:push:fetch_failures:{}"
PUSH_FETCH_FAILURES_TTL = 600  # seconds; failures are consecutive per-minute runs


DEAD_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered", "ExpiredToken"})


class MatchStatus(StrEnum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"
    LIVE = "live"
    TBD = TBD


LIVE_STATUSES = frozenset({MatchStatus.LIVE.value, MatchStatus.ONGOING.value})
FINAL_STATUSES = frozenset(
    {"final", MatchStatus.COMPLETED.value}
)  # Detail pages say "final"; listings say "completed".


class FavoriteType(StrEnum):
    """Favorite entity types stored as plain strings in SQLite."""

    MATCH = "match"
    EVENT = "event"
    TEAM = "team"
    PLAYER = "player"


class Platform(StrEnum):
    """Client platform that owns a push token."""

    IOS = "iOS"
    ANDROID = "android"


FAVORITE_GROUPS = {
    FavoriteType.TEAM: "teams",
    FavoriteType.MATCH: "matches",
    FavoriteType.PLAYER: "players",
    FavoriteType.EVENT: "events",
}


class EventStatus(StrEnum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class VetoAction(StrEnum):
    BAN = "ban"
    PICK = "pick"
    REMAINS = "remains"  # the decider left over after picks/bans; ``team`` is None
    UNKNOWN = "unknown"  # note text the parser didn't recognize; ``map`` holds the raw text


class NewsVideoProvider(StrEnum):
    """Providers supported by hosted news video players."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"


REGION_NAME_MAPPING = {
    # "gc": "Game Changers",
    "la-s": "Latin America South",
    "la-n": "Latin America North",
    "mena": "MENA",
    "asia-pacific": "Asia-Pacific",
}


class SearchCategory(StrEnum):
    ALL = "all"
    TEAM = "teams"
    PLAYER = "players"
    EVENT = "events"
    SERIES = "series"


# Hard cap on the maximum number of pages fetched in any pagination mode.
# Bounded mode: pages are clamped to min(param, MAX_PAGINATION_PAGES).
# Full-history mode (param <= 0): crawl stops after this many total pages.
MAX_PAGINATION_PAGES = 50

# Timeouts and TTLs (in seconds)
# TTLs should be >= 2× cron interval to survive a missed run
REQUEST_TIMEOUT = 60.0
CACHE_TTL_RANKINGS = 3600  # 1 hour (cron: every 30 min)
CACHE_TTL_MATCHES = 600  # 10 minutes (cron: every 5 min)
CACHE_TTL_EVENTS = 3600  # 1 hour (cron: every 30 min)
CACHE_TTL_NEWS = 3600  # 1 hour (cron: every 30 min)
CACHE_TTL_STANDINGS = 90000  # 25 hours (cron: daily at midnight)
# By-id team/player pages have no cron; these are live-fetched on demand (heavily by
# the /ask agent). A very small TTL collapses the burst of duplicate fetches within a
# single agent run and rapid repeats, without serving stale data.
CACHE_TTL_TEAM = 60  # 1 minute
CACHE_TTL_PLAYER = 60  # 1 minute
# Match detail is the most-requested VLR page (app widgets poll it in bursts). Cached only on
# the API route, so the live-push cron still fetches fresh; short enough for live scores.
CACHE_TTL_MATCH = 30

MAX_FAVORITES_PER_GROUP = 200
MAX_TOKEN_LENGTH = 4096
ACTIVITY_ATTRIBUTES_TYPE = "MatchActivityAttributes"
