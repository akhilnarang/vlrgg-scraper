from datetime import timedelta
from enum import StrEnum

PREFIX = "https://www.vlr.gg"

EVENTS_URL = f"{PREFIX}/events/?tier=all"

EVENTS_TIER_URL = f"{PREFIX}/events/?tier={{}}&page={{}}"

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
NA = "n/a"  # VLR's navigation placeholder for a game that has no stats panel yet
# Entity IDs as VLR names them: digits with no leading zero.
ID_REGEX = r"^[1-9][0-9]{0,9}$"
TEST_MATCH_ID = "3141592653"
TEST_TICK_KEY = "vlrgg:push:test_tick"
# A tracked match whose page fails this many cron runs in a row is ended (e.g. VLR blocked our IP).
PUSH_FETCH_FAILURE_LIMIT = 3
PUSH_FETCH_FAILURES_KEY = "vlrgg:push:fetch_failures:{}"
PUSH_FETCH_FAILURES_TTL = 600  # seconds; failures are consecutive per-minute runs
# The live push cron fetches the VLR listing only while a cached match is live or starts within this lead.
PUSH_LISTING_LEAD = timedelta(minutes=15)
VIDEO_SCORE_KEY = "vlrgg:push:video_score"
VIDEO_ROUNDS_KEY = "vlrgg:push:video_rounds:{}:{}"  # verified round history by match ID and map number
VIDEO_ROUNDS_TTL = 86400  # retain completed maps through long series and stream breaks
VIDEO_DELIVERED_KEY = "vlrgg:push:video_delivered"
PUSH_REFRESH_KEY = "vlrgg:push:refresh:{}"  # per-match cooldown for unchanged FCM refreshes
PUSH_REFRESH_SECONDS = 60  # seconds between unchanged-state follower refreshes
PUSH_DETAILS_KEY = "vlrgg:push:details"  # each tracked match's last fetched details, for video pushes
PUSH_DETAILS_TTL = 3600
VIDEO_SCORE_TTL = 3600  # keeps the last video score as VLR's floor through breaks between maps
VIDEO_STALE_SECONDS = 90
VIDEO_MAX_LEAD_ROUNDS = 3  # rounds the tracker may lead the rounds VLR shows on a map


DEAD_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered", "ExpiredToken"})


class IdMapKind(StrEnum):
    TEAM = "team"
    EVENT = "event"


class VideoStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class TeamSide(StrEnum):
    """Broadcast score bar side; blue is left."""

    BLUE = "blue"
    RED = "red"


class VideoPauseKind(StrEnum):
    """Broadcast tracker pause kind."""

    TECH_PAUSE = "tech_pause"
    TIMEOUT = "timeout"
    HALFTIME = "halftime"
    PAUSE = "pause"


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

FAVORITE_MATCHES_PER_ENTITY = 5  # matches returned per favorite, for live + upcoming and again for results
FAVORITE_RESULTS_WINDOW = timedelta(hours=24)  # how far back optional favorite results reach
FAVORITE_PLAYER_REFRESH_DELAY = 2  # seconds between player page fetches in the players cron
FAVORITE_PLAYER_REFRESH_BATCH = 10  # most player pages fetched per players cron run (every 30 min)
FAVORITE_PLAYER_MAX_AGE = 86400  # seconds before a stored favorite player is fetched again


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


class RoundWinner(StrEnum):
    """Round winner as VLR's map panel orders the teams."""

    TEAM1 = "team1"
    TEAM2 = "team2"


class Circuit(StrEnum):
    """Competition circuit an event's matches count towards."""

    VCT = "vct"
    VCL = "vcl"  # Challengers
    T3 = "t3"
    GC = "gc"  # Game Changers
    COLLEGIATE = "collegiate"
    OFFSEASON = "offseason"
    OTHER = "other"


# Event tier filter values, as shown by the tier tabs on VLR's /events page.
TIER_CIRCUITS = {
    "60": Circuit.VCT,
    "61": Circuit.VCL,
    "62": Circuit.T3,
    "63": Circuit.GC,
    "64": Circuit.COLLEGIATE,
    "67": Circuit.OFFSEASON,
}


class Region(StrEnum):
    """Riot competitive region (VCT international league)."""

    AMERICAS = "americas"
    EMEA = "emea"
    PACIFIC = "pacific"
    CHINA = "china"


class RankingScope(StrEnum):
    """Whether a result moves the series Elo or the per-map Elo."""

    MATCH = "match"
    MAP = "map"


class RankingSort(StrEnum):
    ELO = "elo"
    MAP_ELO = "map_elo"
    MATCHES = "matches"
    WIN_RATE = "win_rate"


class RankingOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class PredictionSourceKind(StrEnum):
    MODEL = "model"
    ELO = "elo"


class PredictionRating(StrEnum):
    SERIES = "series"
    MAP = "map"


class PredictionWarningCode(StrEnum):
    UNKNOWN_TEAM = "unknown_team"
    LOW_COVERAGE = "low_coverage"
    UNKNOWN_PATCH = "unknown_patch"
    NO_ELIGIBLE_HISTORY = "no_eligible_history"
    NO_TEAM_MAP_HISTORY = "no_team_map_history"
    LIMITED_TEAM_MAP_HISTORY = "limited_team_map_history"
    MAP_NOT_IN_MODEL = "map_not_in_model"
    UNVALIDATED_MAP_FALLBACK = "unvalidated_map_fallback"
    ELO_FALLBACK = "elo_fallback"
    UNKNOWN = "unknown"


class PredictionFallbackReason(StrEnum):
    MODEL_NOT_CONFIGURED = "model_not_configured"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_HTTP_ERROR = "model_http_error"
    MODEL_INVALID_RESPONSE = "model_invalid_response"


ELO_BASE = 1500.0
ELO_K = 48.0
ELO_MAP_ALPHA = 0.3  # weight of a series' map-win share in the observed result
ELO_ALGORITHM = "k48-hnone-m0.3-r0"
RANKING_MIN_MATCHES = 5  # series inside RANKING_WINDOW_DAYS that make a team rankable
RANKING_ACTIVE_DAYS = 90  # a team must have played within this many days to be active
RANKING_WINDOW_DAYS = 180  # series older than this do not count towards RANKING_MIN_MATCHES
# Match-scope ledger rows carry no map; the archive's sentinel for "the series result".
MATCH_SCOPE_MAP_INDEX = -1
# Timezone matches are dated in, matching the source's local match day.
MATCH_TIMEZONE = "America/New_York"


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
MAP_WIN_ROUNDS = 13  # rounds needed to win a map, with a two-round lead in overtime
MAX_TOKEN_LENGTH = 4096
ACTIVITY_ATTRIBUTES_TYPE = "MatchActivityAttributes"
APNS_START_EXPIRATION = 600  # seconds APNs keeps a push-to-start for a device it cannot reach
APNS_STORE_LATEST_BROADCAST = 1  # channel message-storage-policy: keep the latest update for offline devices
# Seconds an ended Live Activity remains on the Lock Screen; APNs' default of four hours would
# leave a stale final card visible long after the match, beside any later live card.
APNS_DISMISSAL_SECONDS = 300
