from enum import Enum

PREFIX = "https://www.vlr.gg"

VLR_IMAGE = "/img/"

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


class MatchStatus(str, Enum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"
    LIVE = "live"
    TBD = TBD


class EventStatus(str, Enum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"
    PAUSED = "paused"
    UNKNOWN = "unknown"


REGION_NAME_MAPPING = {
    # "gc": "Game Changers",
    "la-s": "Latin America South",
    "la-n": "Latin America North",
    "mena": "MENA",
    "asia-pacific": "Asia-Pacific",
}


class SearchCategory(str, Enum):
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
