from enum import Enum

PREFIX = "https://www.vlr.gg"

VLR_IMAGE = "/img/"

EVENTS_URL = f"{PREFIX}/events/"

EVENT_URL_WITH_ID = f"{PREFIX}/event/{{}}"

EVENT_URL_WITH_ID_MATCHES = f"{PREFIX}/event/matches/{{}}/?series_id=all"

MATCH_URL_WITH_ID = f"{PREFIX}/{{}}"

UPCOMING_MATCHES_URL = f"{PREFIX}/matches"

PAST_MATCHES_URL = f"{PREFIX}/matches/results"

NEWS_URL = f"{PREFIX}/news"

TEAM_URL = f"{PREFIX}/team/{{}}"

TEAM_UPCOMING_MATCHES_URL = f"{PREFIX}/team/matches/{{}}/?group=upcoming"

TEAM_COMPLETED_MATCHES_URL = f"{PREFIX}/team/matches/{{}}/?group=completed"

PLAYER_URL = f"{PREFIX}/player/{{}}/?timespan=all"

RANKINGS_URL = f"{PREFIX}/rankings"

RANKING_URL_REGION = f"{PREFIX}{{}}"

SEARCH_URL = f"{PREFIX}/search?q={{}}&type={{}}"

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
