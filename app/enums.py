"""The string enums shared across the API, the scrapers and the crons."""

from enum import StrEnum


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
    TBD = "tbd"


class FavoriteType(StrEnum):
    """Favorite entity types stored as plain strings in the database."""

    MATCH = "match"
    EVENT = "event"
    TEAM = "team"
    PLAYER = "player"


class Platform(StrEnum):
    """Client platform that owns a push token."""

    IOS = "iOS"
    ANDROID = "android"


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


class RiotMatchState(StrEnum):
    """A Riot web series' own state."""

    UNSTARTED = "unstarted"
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"


class RiotGameState(StrEnum):
    """One Riot web game's own state; ``unneeded`` means the series ended before it was played."""

    UNSTARTED = "unstarted"
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"
    UNNEEDED = "unneeded"


class VodProvider(StrEnum):
    """A VOD's provider, as Riot names it."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"
    AFREECATV = "afreecatv"


class BracketSourceKind(StrEnum):
    """Where a bracket slot's participant comes from."""

    MATCH = "match"
    SEED = "seed"


class BracketSourceResult(StrEnum):
    """Which side of a feeding match a bracket slot receives."""

    WINNER = "winner"
    LOSER = "loser"


class BracketStatus(StrEnum):
    """What a bracket response's sections do and do not cover."""

    OK = "ok"  # every mapped tournament answered
    NO_BRACKET = "no_bracket"  # Riot answered, and no readable section belongs to this event
    UNAVAILABLE = "unavailable"  # a mapping or fetch gap makes the answer unknown
    STALE = "stale"  # sections returned, but a mapped tournament could not be read


class InsightKind(StrEnum):
    """A write-once artifact generated for one match."""

    PREDICTION = "prediction"
    PRE_SUMMARY = "pre_summary"
    POST_SUMMARY = "post_summary"


class NewsVideoProvider(StrEnum):
    """Providers supported by hosted news video players."""

    YOUTUBE = "youtube"
    TWITCH = "twitch"


class SearchCategory(StrEnum):
    ALL = "all"
    TEAM = "teams"
    PLAYER = "players"
    EVENT = "events"
    SERIES = "series"
