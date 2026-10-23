from datetime import timedelta

from app.enums import Circuit, FavoriteType, MatchStatus

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
TEST_TICK_KEY = "valesports:push:test_tick"
# A tracked match whose page fails this many cron runs in a row is ended (e.g. VLR blocked our IP).
PUSH_FETCH_FAILURE_LIMIT = 3
PUSH_FETCH_FAILURES_KEY = "valesports:push:fetch_failures:{}"
PUSH_FETCH_FAILURES_TTL = 600  # seconds; failures are consecutive per-minute runs
# Riot's live list lingers ~11 minutes after a series, so it may explain a failed VLR read for
# a few minutes past the last successful one before the normal fetch-failure path takes over.
PUSH_RIOT_LIVE_GRACE_SECONDS = 300
PUSH_RIOT_LIVE_SINCE_KEY = "valesports:push:riot_live_since:{}"  # when a match's failed reads began
PUSH_RIOT_LIVE_SINCE_TTL = 3600  # outlives the grace, so an expired window cannot restart
# The live push cron fetches the VLR listing only while a cached match is live or starts within this lead.
PUSH_LISTING_LEAD = timedelta(minutes=15)
VIDEO_SCORE_KEY = "valesports:tracker:score"
VIDEO_ROUNDS_KEY = "valesports:tracker:rounds:{}:{}"  # verified round history by match ID and map number
VIDEO_ROUNDS_TTL = 86400  # retain completed maps through long series and stream breaks
VIDEO_DELIVERED_KEY = "valesports:tracker:delivered"
VIDEO_CONTEXT_KEY = "valesports:tracker:context"  # VLR match ID the tracker last asked context for
VIDEO_CONTEXT_TTL = 180  # the tracker refreshes context every 60 s while Riot lists a live match
VIDEO_FALLBACK_KEY = "valesports:tracker:fallback:{}"  # when VLR began pushing the tracker's match
VIDEO_FALLBACK_TTL = 300  # renewed each cron run while the gap lasts
VIDEO_FALLBACK_ALERT_SECONDS = 1500  # longer than a break between maps plus a 0-0 opening
VIDEO_FALLBACK_ALERTED_KEY = "valesports:tracker:fallback_alerted:{}"
VIDEO_FALLBACK_ALERT_INTERVAL = 3600
VIDEO_CODES_KEY = "valesports:tracker:codes"  # team codes the tracker last asked context for, resolved or not
VIDEO_CODES_TTL = 180  # the tracker asks every 60 s; three missed requests mean it stopped asking
VIDEO_UNRESOLVED_KEY = "valesports:tracker:unresolved"  # the codes no VLR match fits, and since when
VIDEO_UNRESOLVED_TTL = 300  # renewed by every failed request in a row
VIDEO_UNRESOLVED_ALERT_SECONDS = 1800  # above the 19-minute longest observed wait for VLR to list a live match
VIDEO_UNRESOLVED_ALERTED_KEY = "valesports:tracker:unresolved_alerted"
VIDEO_UNRESOLVED_ALERT_INTERVAL = 3600
PUSH_REFRESH_KEY = "valesports:push:refresh:{}"  # per-match cooldown for unchanged FCM refreshes
PUSH_REFRESH_SECONDS = 60  # seconds between unchanged-state follower refreshes
PUSH_DETAILS_KEY = "valesports:vlr:details"  # each tracked match's last fetched details, for video pushes
PUSH_DETAILS_TTL = 3600
VIDEO_SCORE_TTL = 3600  # keeps the last video score as VLR's floor through breaks between maps
VIDEO_STALE_SECONDS = 90
VIDEO_MAX_LEAD_ROUNDS = 3  # rounds the tracker may lead the rounds VLR shows on a map


DEAD_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered", "ExpiredToken"})

LIVE_STATUSES = frozenset({MatchStatus.LIVE.value, MatchStatus.ONGOING.value})
FINAL_STATUSES = frozenset(
    {"final", MatchStatus.COMPLETED.value}
)  # Detail pages say "final"; listings say "completed".

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

# Event tier filter values, as shown by the tier tabs on VLR's /events page.
TIER_CIRCUITS = {
    "60": Circuit.VCT,
    "61": Circuit.VCL,
    "62": Circuit.T3,
    "63": Circuit.GC,
    "64": Circuit.COLLEGIATE,
    "67": Circuit.OFFSEASON,
}

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


PREDICTION_TIMEOUT_SECONDS = 2.0  # a slower prediction service answer falls back to Elo
RIOT_MATCH_API_URL = "https://esports.api.riotgames.com/val/match/v1/"
# From Riot's val-content-v1.
RIOT_MAP_NAMES = {
    "Ascent": "Ascent",
    "Bonsai": "Split",
    "Canyon": "Fracture",
    "Duality": "Bind",
    "Foxtrot": "Breeze",
    "Infinity": "Abyss",
    "Jam": "Lotus",
    "Juliett": "Sunset",
    "Pitt": "Pearl",
    "Plummet": "Summit",
    "Port": "Icebox",
    "Rook": "Corrode",
    "Triad": "Haven",
}
RIOT_LINK_LOOKBACK = timedelta(hours=8)
RIOT_LINK_LEAD = timedelta(minutes=30)
RIOT_LINK_MIN_CONFIDENCE = 0.95
RIOT_LINK_MODEL_TIMEOUT = 30.0
# arq stops a job after 300 s; the cron stops checking games before that and stores what it found.
RIOT_RUN_BUDGET = timedelta(seconds=240)
# Games turned down or contested are not asked about again while Riot still lists them.
RIOT_REJECTED_KEY = "valesports:riot:rejected:{}"
RIOT_REJECTED_TTL = timedelta(hours=13)

# Riot's watch-page service. ``sport=val`` is mandatory: without it Riot silently answers for another sport.
RIOT_WEB_API_URL = "https://esports-api.service.valorantesports.com/persisted/val/"
RIOT_WEB_PARAMS = {"hl": "en-US", "sport": "val"}
RIOT_WEB_TIMEOUT = 10.0
RIOT_WEB_LIVE_TIMEOUT = 5.0  # inside the minute live-push cron
RIOT_WEB_LIVE_KEY = "valesports:riot_web:live"
RIOT_WEB_LIVE_TTL = 60  # seconds; Riot's CDN caches getLiveDetails for the same window
RIOT_WEB_REQUEST_TIMEOUT = 5.0  # on-demand standings fetch in the brackets route
RIOT_WEB_SCHEDULE_MAX_PAGES = 5  # 80 events a page
RIOT_WEB_SCHEDULE_LOOKBACK = timedelta(days=3)
RIOT_WEB_LINK_WINDOW = timedelta(minutes=120)
RIOT_WEB_LINK_FALLBACK_WINDOW = timedelta(minutes=360)  # only with exactly one candidate
RIOT_WEB_DETAILS_BATCH = 30
RIOT_WEB_DETAILS_REFRESH = timedelta(hours=1)  # completed matches gain VODs/locales for a while
RIOT_WEB_DETAILS_SETTLE = timedelta(hours=48)  # after this a fetched match is never refetched
RIOT_WEB_VODS_TIMEOUT = 1.0  # a match request waits at most this long for its stored VOD offsets
RIOT_WEB_STANDINGS_WINDOW = timedelta(days=2)  # tournaments with a match this close to now are refreshed
RIOT_WEB_STANDINGS_RETRY = timedelta(hours=24)  # an ended/empty tournament is not refetched sooner
RIOT_WEB_KEY_ALERTED_KEY = "valesports:riot_web:key_alerted"
RIOT_WEB_KEY_ALERT_INTERVAL = 3600
# VOD ids go into playback URLs, so only these shapes are accepted.
YOUTUBE_VOD_ID_REGEX = r"^[A-Za-z0-9_-]{11}$"
TWITCH_VOD_ID_REGEX = r"^\d{1,15}$"
# Converted response; Riot's CDN caps at 60 s.
RIOT_WEB_BRACKETS_KEY = "valesports:riot_web:brackets:{}"
CACHE_TTL_RIOT_WEB_BRACKETS = 60
RIOT_WEB_NAME_SUFFIXES = frozenset({"esports", "gaming", "clan"})
# Riot display name -> VLR display names; a set, because Riot's "Fluxo" is two VLR teams.
RIOT_WEB_TEAM_ALIASES: dict[str, frozenset[str]] = {
    "KRÜ Visa": frozenset({"KRÜ Esports"}),
    "Made In Brazil": frozenset({"MIBR GC"}),
    "FENNEL HOTELAVA": frozenset({"FENNEL GC"}),
    "Fusion": frozenset({"FUSION X"}),
    "Bon Bon Bum Contra": frozenset({"Contra"}),
    "Fluxo": frozenset({"LA MASIA", "Fluxo W7M"}),
    "NAVI": frozenset({"Natus Vincere"}),
    "VISA KRÜ Blaze": frozenset({"KRÜ BLAZE"}),
    "Nos Sweat Blossom": frozenset({"No Sweat Blossom"}),
    "Team Liquid VISA": frozenset({"Team Liquid Brazil"}),
    "Dplus KIA": frozenset({"Dplus Esports"}),
    "RED Canids Kalunga": frozenset({"RED Canids"}),
    "YiJing": frozenset({"Yi-Jing"}),
    "FULL SENSE SISU": frozenset({"sisu"}),
    "Justice For Tomorrow": frozenset({"JFT GC"}),
    "Keep Best Gaming": frozenset({"KeepBest Gaming"}),
    "NAVI Junior": frozenset({"Natus Vincere Junior"}),
    "Joblife GC": frozenset({"Joblife Nanas"}),
    "KIWOOM DRX Academy": frozenset({"DRX Academy"}),
    "T1 Esports Academy": frozenset({"T1 Academy"}),
}

# The prediction and pre-match summary are generated only within this lead before the scheduled start.
INSIGHTS_PRE_MATCH_WINDOW = timedelta(hours=24)
# The post-match summary is generated only for a match that started within this long.
INSIGHTS_POST_MATCH_WINDOW = timedelta(days=7)
# One generation per match at a time. A generation is cut off after its provider calls' own timeouts plus
# this overhead for its reads and writes, and its claim expires this margin later, so no other starts meanwhile.
INSIGHTS_CLAIM_KEY = "vlrgg:insights:claim:{}"
INSIGHTS_GENERATION_OVERHEAD_SECONDS = 10.0
INSIGHTS_CLAIM_MARGIN_SECONDS = 10.0
INSIGHTS_CLEANUP_SECONDS = 2.0  # bound on the Redis calls that cool down or release after a generation
# Shutdown waits this long for running generations before cancelling them.
INSIGHTS_SHUTDOWN_SECONDS = 10.0
# A request stops waiting on generation this long before its deadline, to read what was stored meanwhile.
INSIGHTS_READ_RESERVE_SECONDS = 1.0
# A failed attempt at one artifact is not retried for this long, or a summary
# skipped for lack of useful data for the longer time.
INSIGHTS_COOLDOWN_KEY = "vlrgg:insights:cooldown:{}:{}"
INSIGHTS_COOLDOWN_SECONDS = 300
INSIGHTS_NO_DATA_COOLDOWN_SECONDS = 3600
INSIGHTS_HISTORY_DAYS = 180  # ledger history the pre-match summary draws on
SUMMARY_MAX_CHARS = 1200  # a longer model answer is rejected, never truncated

REGION_NAME_MAPPING = {
    # "gc": "Game Changers",
    "la-s": "Latin America South",
    "la-n": "Latin America North",
    "mena": "MENA",
    "asia-pacific": "Asia-Pacific",
}

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
# Seconds an ended Live Activity stays on the Lock Screen, so a follower who checks their phone
# hours after the final still sees the result; four hours is the most ActivityKit allows.
APNS_DISMISSAL_SECONDS = 4 * 60 * 60
