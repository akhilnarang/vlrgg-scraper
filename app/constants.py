from enum import Enum

PREFIX = "https://www.vlr.gg"

VLR_IMAGE = "/img/vlr/tmp/vlr.png"

EVENTS_URL = f"{PREFIX}/events/"

EVENT_URL_WITH_ID = f"{PREFIX}/event/{{}}"

EVENT_URL_WITH_ID_MATCHES = f"{PREFIX}/event/matches/{{}}/?series_id=all"

MATCH_URL_WITH_ID = f"{PREFIX}/{{}}"

NEWS_URL = f"{PREFIX}/news"


class MatchStatus(str, Enum):
    COMPLETED = "completed"
    ONGOING = "ongoing"
    UPCOMING = "upcoming"
