"""Join Riot's stored per-map VOD offsets onto a VLR match's maps.

Riot's ``getEventDetails`` gives each game a number and its streams' ``startMillis``, but no map
name and no per-map score, so the numbers can only be trusted when they agree with VLR's. Riot is
only ever an overlay: VLR's own ``videos.vods`` stay exactly as they are.
"""

import asyncio
import logging
import re

from pydantic import HttpUrl

from app.constants import RIOT_WEB_VODS_TIMEOUT, TWITCH_VOD_ID_REGEX, YOUTUBE_VOD_ID_REGEX
from app.core import connections
from app.core.config import settings
from app.enums import RiotGameState, VodProvider
from app.schemas.matches import MapVods, MatchWithDetails, VodLink
from app.schemas.riot_web import StoredDetails, StoredGame, StoredVod
from app.services import riot_web_store
from app.services.riot_web_link import is_game_changers, team_keys

logger = logging.getLogger(__name__)

_VOD_ID_PATTERNS = {
    VodProvider.YOUTUBE: YOUTUBE_VOD_ID_REGEX,
    VodProvider.TWITCH: TWITCH_VOD_ID_REGEX,
}


async def map_vods(match_id: str, details: MatchWithDetails) -> list[MapVods]:
    """Read the Riot offsets stored for a VLR match and align them with its maps.

    Returns nothing unless ``RIOT_WEB_API_KEY`` is set, the match is linked to a series we have
    fetched, and that series' completed games and score agree with VLR's.

    :param match_id: VLR match id.
    :param details: The match's VLR details.
    :return: One entry per played map that has a usable VOD, best link first, or an empty list.
    """
    if not settings.RIOT_WEB_API_KEY:
        return []
    sessions = connections.subscription_sessions
    if sessions is None:
        return []
    try:
        async with asyncio.timeout(RIOT_WEB_VODS_TIMEOUT):
            async with sessions() as session:
                stored = await riot_web_store.details_for_vlr(session, match_id)
    except Exception:
        # A Riot extra never fails the VLR response it rides on.
        logger.warning("could not read Riot's stored VOD offsets for match %s", match_id, exc_info=True)
        return []
    if stored is None or not _aligned(details, stored):
        return []
    by_map = _links_by_map(stored)
    return [
        MapVods(map_number=item.number, links=by_map[item.number]) for item in details.data if by_map.get(item.number)
    ]


def _aligned(details: MatchWithDetails, stored: StoredDetails) -> bool:
    """Check that VLR and Riot agree on which maps were played and on each team's series score.

    :param details: The match's VLR details.
    :param stored: The linked series' stored details.
    :return: True when Riot's completed games are exactly VLR's played maps at the same numbers,
        and every VLR team carries the score Riot gave that team.
    """
    played = {item.number for item in details.data if item.winner}
    completed = {game.number for game in stored.games if game.state == RiotGameState.COMPLETED}
    return played == completed and _scores_agree(details, stored)


def _scores_agree(details: MatchWithDetails, stored: StoredDetails) -> bool:
    """Match every Riot score to its VLR team, so a 1-3 is not read as a 3-1.

    Riot names teams differently from VLR, so both sides are joined with the linker's keys.

    :param details: The match's VLR details.
    :param stored: The linked series' stored details.
    :return: True when both sides list the same teams with the same score on each team.
    """
    game_changers = is_game_changers(details.event.series)
    riot = {name: team_keys(name, game_changers, riot=True) for name in stored.game_wins}
    matched = set()
    for team in details.teams:
        keys = team_keys(team.name, game_changers, riot=False)
        names = [name for name, other in riot.items() if keys & other]
        if len(names) != 1:
            return False
        name = names[0]
        if name in matched or (team.score or 0) != stored.game_wins[name]:
            return False
        matched.add(name)
    return len(matched) == len(riot)


def _links_by_map(stored: StoredDetails) -> dict[int, list[VodLink]]:
    """Build every completed map's playable links, dropping maps that have none.

    :param stored: The linked series' stored details.
    :return: Map number to its links, best first.
    """
    by_map = {}
    for game in stored.games:
        if game.state == RiotGameState.COMPLETED and (links := _links(game)):
            by_map[game.number] = links
    return by_map


def _links(game: StoredGame) -> list[VodLink]:
    """Build one game's links in display order: English first, then YouTube before Twitch.

    Riot's order is kept within a group, and a VOD whose id is not a real YouTube or Twitch id
    is dropped before any URL is built from it.

    :param game: One stored game.
    :return: The game's playable links, best first.
    """
    links = []
    seen = set()
    for vod in game.vods:
        if not _safe_parameter(vod):
            continue
        key = (vod.provider, vod.parameter, vod.locale)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            VodLink(
                provider=vod.provider,
                locale=vod.locale,
                start_seconds=vod.start_ms // 1000,
                url=_url(vod),
            )
        )
    links.sort(key=lambda link: (not link.locale.startswith("en-"), link.provider != VodProvider.YOUTUBE))
    return links


def _safe_parameter(vod: StoredVod) -> bool:
    """Check that a VOD's id is one the provider can actually serve.

    :param vod: Stored VOD.
    :return: True for a YouTube id of 11 URL-safe characters or a numeric Twitch id.
    """
    pattern = _VOD_ID_PATTERNS.get(vod.provider)
    return pattern is not None and re.fullmatch(pattern, vod.parameter) is not None


def _url(vod: StoredVod) -> HttpUrl:
    """Build the playback URL that opens a VOD at this map.

    :param vod: Stored VOD.
    :return: A YouTube or Twitch URL with the map's start offset.
    """
    seconds = vod.start_ms // 1000
    if vod.provider == VodProvider.TWITCH:
        return HttpUrl(
            f"https://www.twitch.tv/videos/{vod.parameter}?t={seconds // 3600}h{seconds % 3600 // 60}m{seconds % 60}s"
        )
    return HttpUrl(f"https://www.youtube.com/watch?v={vod.parameter}&t={seconds}s")
