"""LLM-written match summaries: the data each prompt receives, the model call, and output checks."""

import json
import logging

from openai import AsyncOpenAI

from app import utils
from app.constants import SUMMARY_MAX_CHARS
from app.core.config import settings
from app.enums import RoundWinner
from app.schemas.matches import MatchWithDetails

logger = logging.getLogger(__name__)

_SIDES = ("team_a", "team_b")
# Per-map player stats that add up over a series, and those averaged over its rounds.
_TOTALS = ("kills", "deaths", "assists", "first_kills", "first_deaths")
_AVERAGES = ("rating", "acs", "adr", "kast")


def pre_match_input(details: MatchWithDetails, prediction: dict | None, head_to_head: list[dict]) -> dict | None:
    """Assemble what the pre-match summary is written from, if there is something useful to say.

    Teams appear by name only under ``teams`` and elsewhere as ``team_a`` and ``team_b``, so
    no name is ever a key.

    :param details: Upcoming match with two identified teams.
    :param prediction: Stored model prediction for the two teams, if any.
    :param head_to_head: ``history.head_to_head`` series records, counted for the header's first team.
    :return: JSON-ready prompt data, or None without a head-to-head record or a prediction.
    """
    if not head_to_head and prediction is None:
        return None
    sides = {team.id or "": side for side, team in zip(_SIDES, details.teams)}
    data: dict = {
        "teams": {side: team.name for side, team in zip(_SIDES, details.teams)},
        "event": {"series": details.event.series, "stage": details.event.stage},
        "best_of": details.total_maps,
        "scheduled_start": details.event.date.isoformat() if details.event.date else None,
        "head_to_head": [{"team": _SIDES[0], **group} for group in head_to_head],
    }
    if prediction is not None:
        data["prediction"] = {
            "kind": "model estimate of winning the series",
            "win_probability": {
                sides[prediction["team_a_id"]]: prediction["team_a"],
                sides[prediction["team_b_id"]]: prediction["team_b"],
            },
        }
    return data


def post_match_input(details: MatchWithDetails) -> dict | None:
    """Assemble what the post-match summary is written from, if VLR shows the whole series.

    Map panels and round winners follow each map's own team order, so both are aligned
    with the header teams, which appear by name only under ``teams`` and elsewhere as
    ``team_a`` and ``team_b``. Unplayed maps are skipped. Each map's rounds run to its score's
    total, dropping the placeholder rounds VLR renders after a map ends; a played round
    with no recorded winner, or none scraped, stays ``None``.

    :param details: Final match.
    :return: JSON-ready prompt data, or None unless two identified teams have a decisive series
        score that the decided maps, each with round winners and both teams' players, reproduce.
    """
    if len(details.teams) != 2 or not all(team.id for team in details.teams):
        return None
    series = [team.score for team in details.teams]
    if series[0] is None or series[1] is None or series[0] == series[1]:
        return None
    header = [(team.name, team.tag) for team in details.teams]
    wins = [0, 0]
    maps = []
    players: dict[tuple[int, str], dict] = {}
    for item in sorted(details.data, key=lambda entry: entry.number):
        sides = [utils.header_team_index(team.name, header) for team in item.teams]
        if sorted(side for side in sides if side is not None) != [0, 1]:
            return None
        scores = [item.teams[sides.index(side)].score for side in range(2)]
        if scores == [None, None]:
            continue
        if scores[0] is None or scores[1] is None or scores[0] == scores[1]:
            return None
        winner = 0 if scores[0] > scores[1] else 1
        wins[winner] += 1
        played = scores[0] + scores[1]
        sides_by_winner = {RoundWinner.TEAM1: sides[0], RoundWinner.TEAM2: sides[1]}
        rounds: list[int | None] = [None] * played
        for entry in item.rounds:
            if 1 <= entry.round_number <= played:
                rounds[entry.round_number - 1] = sides_by_winner.get(entry.winner)
        members = [
            (side, member)
            for member in item.members
            if (side := utils.header_team_index(member.team, header)) is not None
        ]
        if (
            all(side is None for side in rounds)
            or len(members) != len(item.members)
            or {side for side, _ in members} != {0, 1}
        ):
            return None
        for side, member in members:
            player = players.setdefault(
                (side, member.id or member.name),
                {"name": member.name, "team": _SIDES[side], "agents": [], "rounds": 0}
                | dict.fromkeys(_TOTALS + _AVERAGES, 0),
            )
            player["agents"] += [agent.title for agent in member.agents if agent.title not in player["agents"]]
            player["rounds"] += played
            for stat in _TOTALS:
                player[stat] += getattr(member, stat)
            for stat in _AVERAGES:
                player[stat] += getattr(member, stat) * played
        maps.append(
            {
                "map": item.map,
                "score_a": scores[0],
                "score_b": scores[1],
                "winner": _SIDES[winner],
                "rounds": [
                    {"n": number, "winner": None if side is None else _SIDES[side]}
                    for number, side in enumerate(rounds, start=1)
                ],
            }
        )
    if not maps or wins != series:
        return None
    for player in players.values():
        rounds_played = player.pop("rounds")
        for stat in _AVERAGES:
            player[stat] = round(player[stat] / rounds_played, 2 if stat == "rating" else None)
    return {
        "teams": {side: team.name for side, team in zip(_SIDES, details.teams)},
        "event": {"series": details.event.series, "stage": details.event.stage},
        "result": {"score_a": series[0], "score_b": series[1], "winner": _SIDES[0 if wins[0] > wins[1] else 1]},
        "maps": maps,
        "players": sorted(players.values(), key=lambda player: (player["team"] != _SIDES[0], -player["rating"])),
    }


async def generate(prompt: str, data: dict) -> str | None:
    """Ask the configured model for a summary of some match data.

    :param prompt: Fixed instructions.
    :param data: Match data, sent as the user message.
    :return: The summary, or None when the answer is empty or longer than ``SUMMARY_MAX_CHARS``.
    :raises openai.OpenAIError: If the request fails or times out.
    """
    reasoning = {"reasoning": {"effort": settings.LLM_REASONING_EFFORT}} if settings.LLM_REASONING_EFFORT else {}
    # One attempt within its own timeout, so a generation outliving its request stays bounded.
    async with AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        max_retries=0,
        timeout=settings.SUMMARY_TIMEOUT_SECONDS,
    ) as client:
        response = await client.responses.create(
            model=settings.LLM_MODEL,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
            **reasoning,
        )
    text = (response.output_text or "").strip()
    if not text or len(text) > SUMMARY_MAX_CHARS:
        logger.warning("rejected a %d-character summary", len(text))
        return None
    return text
