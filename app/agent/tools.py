"""LLM tool registry: thin wrappers over the scraper services.

Each `_<tool>` coroutine returns JSON-serializable data (lists/dicts plus the
occasional `TruncatedResult`). `build_tools` exposes the OpenAI Responses-API
tool schemas and a name -> coroutine dispatch map.
"""

from datetime import date

import app.constants as constants
from app.agent.filters import filter_matches, guard_rows
from app.services import search, team, player, rankings, standings, news, events, matches

_MATCH_FILTERS = ["opponent", "event", "stage", "date_from", "date_to", "limit", "roster_core", "opponent_roster_core"]


def _win_loss(score: str) -> str | None:
    """Classify an 'a:b' score from the subject's perspective: 'won', 'lost', or None."""
    try:
        a, b = (int(x) for x in score.replace(" ", "").split(":")[:2])
    except (ValueError, AttributeError):
        return None
    return "won" if a > b else "lost" if a < b else None


async def _search(category: str, term: str):
    """Resolve a team/player/event name to vlr.gg search results."""
    return [r.model_dump(mode="json") for r in await search.get_data(constants.SearchCategory(category), term)]


async def _get_team(id: str, completed_pages: int = 1, opponent=None, event=None, stage=None,
                    date_from=None, date_to=None, limit=None, roster_core=None, opponent_roster_core=None):
    """Team info, roster, and upcoming/completed matches by id, with the completed list filtered and size-guarded."""
    data = (await team.get_team_data(id, completed_pages=completed_pages)).model_dump(mode="json")
    data["completed"] = guard_rows(
        filter_matches(data.get("completed", []), opponent=opponent, event=event, stage=stage,
                       date_from=date_from, date_to=date_to, limit=limit,
                       roster_core=roster_core, opponent_roster_core=opponent_roster_core),
        _MATCH_FILTERS,
    )
    return data


async def _get_player(id: str, match_pages: int = 1, opponent=None, event=None, stage=None,
                      date_from=None, date_to=None, limit=None, roster_core=None, opponent_roster_core=None):
    """Player info, per-agent stats, teams, and match history by id, with the match list filtered and size-guarded."""
    data = (await player.get_player_data(id, match_pages=match_pages)).model_dump(mode="json")
    data["matches"] = guard_rows(
        filter_matches(data.get("matches", []), opponent=opponent, event=event, stage=stage,
                       date_from=date_from, date_to=date_to, limit=limit,
                       roster_core=roster_core, opponent_roster_core=opponent_roster_core),
        _MATCH_FILTERS,
    )
    return data


async def _count_team_matches(id: str, stage=None, event=None, opponent=None, result=None, roster_core=None):
    """Count a team's full completed-match history (optionally filtered, incl. by roster core).

    Returns {played, won, lost, other}. `other` holds matches whose score can't be classified
    (forfeits, walkovers, empty scores), so `played == won + lost + other` always holds.
    """
    data = (await team.get_team_data(id, completed_pages=0)).model_dump(mode="json")
    rows = filter_matches(data.get("completed", []), opponent=opponent, event=event, stage=stage, roster_core=roster_core)
    played = won = lost = other = 0
    for m in rows:
        wl = _win_loss(m.get("score", ""))
        if result and wl != result:
            continue
        played += 1
        if wl == "won":
            won += 1
        elif wl == "lost":
            lost += 1
        else:
            other += 1
    return {"played": played, "won": won, "lost": lost, "other": other}


async def _player_tenure(player_id: str, team_name: str):
    """Derive a player's tenure on a team (first/last match date + span) from full match history."""
    data = (await player.get_player_data(player_id, match_pages=0)).model_dump(mode="json")
    rows = [m for m in data.get("matches", []) if team_name.lower() in (m.get("team", "") or "").lower()]
    if not rows:
        return {"found": False, "team": team_name}
    dates = sorted(str(m.get("date", ""))[:10] for m in rows if m.get("date"))
    first, last = dates[0], dates[-1]
    span = (date.fromisoformat(last) - date.fromisoformat(first)).days
    return {"found": True, "team": team_name, "first": first, "last": last,
            "span_days": span, "matches": len(rows)}


async def _get_rankings():
    """Current vlr.gg team rankings across regions."""
    return [r.model_dump(mode="json") for r in await rankings.ranking_list()]


async def _get_standings(year: int):
    """VCT standings for a given year."""
    return (await standings.standings_list(year)).model_dump(mode="json")


async def _get_news(pages: int = 1):
    """Latest Valorant esports news headlines."""
    return [n.model_dump(mode="json") for n in await news.news_list(pages=pages)]


async def _get_events(redis_client, pages: int = 1):
    """Valorant events (upcoming/ongoing/completed); Redis-gated."""
    return [e.model_dump(mode="json") for e in await events.get_events(redis_client, pages=pages)]


async def _get_matches(redis_client, pages: int = 1):
    """Recently completed matches across all teams; Redis-gated."""
    return [m.model_dump(mode="json") for m in await matches.get_completed_matches(redis_client, pages=pages)]


def _match_filter_props() -> dict:
    """Shared JSON-schema properties for the match filter args on get_team/get_player."""
    return {
        "opponent": {"type": "string", "description": "Filter matches by opponent name (substring)."},
        "event": {"type": "string", "description": "Filter by event name (substring)."},
        "stage": {"type": "string", "description": "Filter by stage (substring, e.g. 'GF')."},
        "date_from": {"type": "string", "description": "ISO date lower bound (YYYY-MM-DD)."},
        "date_to": {"type": "string", "description": "ISO date upper bound (YYYY-MM-DD)."},
        "limit": {"type": "integer", "description": "Max matches to return after filtering."},
        "roster_core": {"type": "string",
                        "description": "Filter by this team/player's roster-core tag (e.g. '#ACM'); the tag changes when the core changes."},
        "opponent_roster_core": {"type": "string", "description": "Filter by the opponent's roster-core tag (e.g. '#YAJ')."},
    }


def build_tools(redis_client):
    """Build the tool registry for the LLM.

    Returns ``(schemas, dispatch)``: the OpenAI Responses-API tool schemas
    (``{type, name, description, parameters}``) and a name -> coroutine dispatch
    map. The Redis-gated tools (``get_events``, ``get_matches``) are included only
    when a ``redis_client`` is provided.
    """
    schemas = [
        {"type": "function", "name": "search",
         "description": "Search vlr.gg for a team, player, or event by name; returns id, name, category. Use first to resolve names.",
         "parameters": {"type": "object", "properties": {
             "category": {"type": "string", "enum": ["teams", "players", "events"]},
             "term": {"type": "string"}}, "required": ["category", "term"]}},
        {"type": "function", "name": "get_team",
         "description": "Team info, roster, upcoming + completed matches by team id. Each match includes a 'roster_core' (this team's roster-core tag, e.g. '#ACM') and 'opponent_roster_core' — the tag changes when a lineup's core changes, so it identifies which roster played. Returns ~50 recent completed by default; pass completed_pages=0 for full history. Use the filters to fetch a slice instead of everything.",
         "parameters": {"type": "object", "properties": {
             "id": {"type": "string"},
             "completed_pages": {"type": "integer", "description": "1=recent (default), 0=all history."},
             **_match_filter_props()}, "required": ["id"]}},
        {"type": "function", "name": "get_player",
         "description": "Player info, per-agent stats, teams, and match history (date, event, stage, team, opponent, score, roster_core, opponent_roster_core) by player id. 'roster_core' is the roster-core tag (e.g. '#ACM') of the player's team that match. ~50 recent matches by default; match_pages=0 for full history. Use filters for a slice.",
         "parameters": {"type": "object", "properties": {
             "id": {"type": "string"},
             "match_pages": {"type": "integer", "description": "1=recent (default), 0=all history."},
             **_match_filter_props()}, "required": ["id"]}},
        {"type": "function", "name": "count_team_matches",
         "description": "Server-side count of a team's completed matches over FULL history, optionally filtered by stage/event/opponent/result/roster_core. Returns {played, won, lost, other} where 'other' is unclassifiable scores (forfeits/walkovers) so played == won+lost+other. Pass a 'roster_core' tag (e.g. '#ACM') to count a specific roster's record. Prefer this over fetching and counting raw matches.",
         "parameters": {"type": "object", "properties": {
             "id": {"type": "string"},
             "stage": {"type": "string"}, "event": {"type": "string"},
             "opponent": {"type": "string"},
             "result": {"type": "string", "enum": ["won", "lost"]},
             "roster_core": {"type": "string", "description": "Roster-core tag to count, e.g. '#ACM'."}}, "required": ["id"]}},
        {"type": "function", "name": "player_tenure",
         "description": "Compute how long a player was on a team from match history (full): first/last match date and span in days. Returns {found, first, last, span_days, matches}.",
         "parameters": {"type": "object", "properties": {
             "player_id": {"type": "string"}, "team_name": {"type": "string"}},
             "required": ["player_id", "team_name"]}},
        {"type": "function", "name": "get_rankings",
         "description": "Current vlr.gg team rankings across regions.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "get_standings",
         "description": "VCT standings for a given year.",
         "parameters": {"type": "object", "properties": {"year": {"type": "integer"}}, "required": ["year"]}},
        {"type": "function", "name": "get_news",
         "description": "Latest Valorant esports news. pages=1 default; use pages>1 for older news.",
         "parameters": {"type": "object", "properties": {"pages": {"type": "integer"}}}},
    ]
    dispatch = {
        "search": _search, "get_team": _get_team, "get_player": _get_player,
        "count_team_matches": _count_team_matches, "player_tenure": _player_tenure,
        "get_rankings": _get_rankings, "get_standings": _get_standings, "get_news": _get_news,
    }
    if redis_client is not None:
        schemas += [
            {"type": "function", "name": "get_events",
             "description": "List Valorant events (upcoming/ongoing/completed). pages=1 default.",
             "parameters": {"type": "object", "properties": {"pages": {"type": "integer"}}}},
            {"type": "function", "name": "get_matches",
             "description": "Recently completed matches across all teams. pages=1 default.",
             "parameters": {"type": "object", "properties": {"pages": {"type": "integer"}}}},
        ]
        dispatch["get_events"] = lambda **kw: _get_events(redis_client, **kw)
        dispatch["get_matches"] = lambda **kw: _get_matches(redis_client, **kw)
    return schemas, dispatch
