"""The rule that links Riot web series to VLR matches: pure functions, no I/O.

Measured on 114 days in ``MAPPING.md``: an equal unordered normalised name pair plus a two-hour
start-time window, then a six-hour window only when exactly one candidate remains. Ambiguity is
never guessed at; an unlinked series simply stays VLR-only.
"""

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app import schemas
from app.constants import (
    RIOT_WEB_LINK_FALLBACK_WINDOW,
    RIOT_WEB_LINK_WINDOW,
    RIOT_WEB_NAME_SUFFIXES,
    RIOT_WEB_TEAM_ALIASES,
    TBD,
)
from app.schemas.riot_web import NamePair

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiotCandidate:
    """A Riot series and what linking it to VLR needs."""

    riot_id: str
    start_time: datetime
    teams: tuple[str, str]
    league_name: str


@dataclass(frozen=True)
class Link:
    """A Riot series and the one VLR match it is."""

    riot_id: str
    vlr_match_id: str
    vlr_event_id: str | None
    minutes: int


def normalise(name: str, game_changers: bool) -> str:
    """Fold a team name to its comparison key.

    The ``GC`` roster marker is dropped before the generic suffixes, so "Barça eSports GC" meets
    Riot's "Barça eSports"; outside a Game Changers competition it is kept, because VLR fields
    both "MIBR" and "MIBR GC".

    :param name: Team display name.
    :param game_changers: Whether the competition is a Game Changers event.
    :return: Lowercase ASCII words, without a trailing generic suffix.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = re.sub(r"[^a-z0-9]+", " ", folded).split()
    if game_changers:
        tokens = [token for token in tokens if token != "gc"]
    while tokens and tokens[-1] in RIOT_WEB_NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def is_game_changers(competition: str) -> bool:
    """Detect a Game Changers competition from either side's name for it.

    VLR calls it the event and Riot the league, so one substring rule keeps both readings
    comparable; a different rule per side would let a GC roster meet its main-roster fixture.

    :param competition: VLR event name or Riot league name.
    :return: True when the name marks a Game Changers competition.
    """
    return "game changers" in competition.casefold()


def _alias_index(game_changers: bool) -> dict[str, frozenset[str]]:
    """Normalise the alias table for one GC reading.

    Keeping both readings in one index leaks a GC roster's alias to the main roster: Riot's
    ``Made In Brazil`` would meet VLR's ``MIBR`` outside a Game Changers competition.

    :param game_changers: Whether the competition is a Game Changers event.
    :return: Riot key to VLR keys, both normalised under the same reading.
    """
    index: dict[str, set[str]] = {}
    for riot_name, vlr_names in RIOT_WEB_TEAM_ALIASES.items():
        key = normalise(riot_name, game_changers)
        for vlr_name in vlr_names:
            value = normalise(vlr_name, game_changers)
            if key and value and key != value:
                index.setdefault(key, set()).add(value)
    return {key: frozenset(values) for key, values in index.items()}


_ALIASES = {game_changers: _alias_index(game_changers) for game_changers in (False, True)}


def team_keys(name: str, game_changers: bool, *, riot: bool) -> frozenset[str]:
    """List every key a team name may be known by.

    :param name: Team display name.
    :param game_changers: Whether the competition is a Game Changers event.
    :param riot: Whether the name is Riot's, so its alias table applies.
    :return: The normalised name and, for Riot, its VLR aliases.
    """
    key = normalise(name, game_changers)
    aliases = _ALIASES[game_changers].get(key, frozenset()) if riot else frozenset()
    return frozenset(filter(None, {key} | aliases))


def _is_tbd(name: str) -> bool:
    return name.casefold() == TBD


def link(riot: Sequence[RiotCandidate], vlr: Sequence[schemas.Match], claimed: set[str]) -> list[Link]:
    """Link each Riot series to the one VLR match it is, never guessing.

    :param riot: Riot series, usually the unlinked rows whose start time is near now.
    :param vlr: Cached VLR listing; its ``time`` is UTC, unlike the site's ``data-utc-ts``.
    :param claimed: VLR match IDs already linked in the database.
    :return: One link per unambiguously matched Riot series. A series with no candidate, several
        candidates, or a claim another series also makes is left unlinked.
    """
    entries = []
    for match in {match.id: match for match in vlr}.values():
        if _is_tbd(match.team1.name) or _is_tbd(match.team2.name):
            continue
        game_changers = is_game_changers(match.event)
        pair = NamePair(
            first=team_keys(match.team1.name, game_changers, riot=False),
            second=team_keys(match.team2.name, game_changers, riot=False),
        )
        if pair.first and pair.second:
            entries.append((match, pair, game_changers, match.id in claimed))

    links = []
    name_pairs = tier1_pairs = 0
    for candidate in sorted(riot, key=lambda series: series.start_time):
        if any(_is_tbd(name) for name in candidate.teams):
            continue
        game_changers = is_game_changers(candidate.league_name)
        pair = NamePair(
            first=team_keys(candidate.teams[0], game_changers, riot=True),
            second=team_keys(candidate.teams[1], game_changers, riot=True),
        )
        if not pair.first or not pair.second:
            continue
        matched = [
            (match, is_claimed)
            for match, other, other_game_changers, is_claimed in entries
            if other_game_changers == game_changers and pair.matches(other)
        ]
        if not matched:
            continue
        for match, is_claimed in matched:
            if is_claimed:
                logger.info(
                    "Riot series %s fits VLR %s, which is already linked; not linking", candidate.riot_id, match.id
                )
        available = [match for match, is_claimed in matched if not is_claimed]
        if not available:
            continue
        name_pairs += 1
        deltas = [(match, match.time - candidate.start_time) for match in available]
        inside = [(match, delta) for match, delta in deltas if abs(delta) <= RIOT_WEB_LINK_WINDOW]
        if inside:
            tier1_pairs += 1
            if len(inside) > 1:
                logger.warning(
                    "Riot series %s fits %d VLR matches inside %s; linking none",
                    candidate.riot_id,
                    len(inside),
                    RIOT_WEB_LINK_WINDOW,
                )
                continue
            spans = inside
        else:
            spans = [(match, delta) for match, delta in deltas if abs(delta) <= RIOT_WEB_LINK_FALLBACK_WINDOW]
            if not spans:
                continue
            if len(spans) > 1:
                logger.warning(
                    "Riot series %s fits %d VLR matches inside %s; linking none",
                    candidate.riot_id,
                    len(spans),
                    RIOT_WEB_LINK_FALLBACK_WINDOW,
                )
                continue
        match, delta = spans[0]
        links.append(
            Link(
                riot_id=candidate.riot_id,
                vlr_match_id=match.id,
                vlr_event_id=match.event_id,
                minutes=round(delta.total_seconds() / 60),
            )
        )

    shared = {vlr_match_id for vlr_match_id, count in Counter(link.vlr_match_id for link in links).items() if count > 1}
    for vlr_match_id in shared:
        riot_ids = [link.riot_id for link in links if link.vlr_match_id == vlr_match_id]
        logger.warning("Riot series %s all fit VLR %s; linking none", riot_ids, vlr_match_id)
    links = [link for link in links if link.vlr_match_id not in shared]

    if name_pairs >= 3 and not tier1_pairs:
        logger.error("Riot and VLR times disagree for every name match; check TIMEZONE against the host zone")
    return links
