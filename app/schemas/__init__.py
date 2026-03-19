from pydantic import TypeAdapter

from .events import Event, EventWithDetails
from .matches import Match, MatchTeam, MatchWithDetails
from .news import NewsItem, NewsArticle
from .player import Player
from .rankings import Ranking, TeamRanking
from .search import SearchResult
from .standings import *
from .team import Team
from .version import VersionResponse
from .internal import TeamCache

# Module-level TypeAdapters for fast JSON serialization/deserialization
# Used by cron jobs (dump_json) and endpoints (validate_json) to avoid
# the slow json.dumps(model_dump(), default=jsonable_encoder) round-trip.
MatchListAdapter = TypeAdapter(list[Match])
EventListAdapter = TypeAdapter(list[Event])
NewsListAdapter = TypeAdapter(list[NewsItem])
RankingListAdapter = TypeAdapter(list[Ranking])
