"""Structural invariants for live VLR parser output.

These are pure functions: no network, no fixtures. They raise `ContractViolation`
when the parser has structurally broken (mass nulls, empty lists, format changes)
and stay silent when only the data has churned (scores changing, matches moving
between lists).

They exist because VLR renames HTML classes without notice and the parsers fall
back to `0`, `""`, `None`, or `[]` rather than raising -- output that is
schema-valid and entirely useless. Pydantic cannot catch that; only value-level
invariants can.

Every function here MUST have a mutation test in `test_contracts_sensitivity.py`
proving it fires. A contract that cannot fail is a green light wired to nothing.
"""

import re
from collections.abc import Callable, Sequence
from typing import Any


SCORE_RE = re.compile(r"^\d+\s*:\s*\d+$")

# VLR shows forfeits/walkovers in place of a numeric score on some cards.
SCORE_OR_FORFEIT_RE = re.compile(r"^(\d+\s*:\s*\d+|.*\bff\b.*|.*\bw/?o\b.*)$", re.IGNORECASE)


class ContractViolation(AssertionError):
    """A structural invariant failed: the parser is likely broken."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation(message)


def _require_ratio(
    items: Sequence[Any],
    predicate: Callable[[Any], bool],
    threshold: float,
    what: str,
    hint: str,
) -> None:
    """Require that at least `threshold` of `items` satisfy `predicate`.

    Ratios rather than absolutes: one team legitimately losing its rank must not
    fire, but every team reporting rank 0 must.
    """
    _require(bool(items), f"{what}: empty list, parser returned nothing -- likely selector break: {hint}")
    try:
        satisfied = sum(1 for item in items if predicate(item))
    except (AttributeError, TypeError) as exc:
        # A missing field or a None comparison means the shape itself moved. Report
        # it as a violation rather than letting a raw traceback obscure the alert.
        raise ContractViolation(f"{what}: could not evaluate ({exc!r}) -- likely selector break: {hint}") from exc
    got = satisfied / len(items)
    _require(
        got >= threshold,
        f"{what}: only {got:.0%} of {len(items)} items satisfied the check "
        f"(need >={threshold:.0%}) -- likely selector break: {hint}",
    )


def _require_min_len(items: Sequence[Any], minimum: int, what: str, hint: str) -> None:
    _require(
        len(items) >= minimum,
        f"{what}: got {len(items)} items, expected >={minimum} -- likely selector break: {hint}",
    )


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_team(team: Any) -> None:
    """Invariants for a team page (`app/services/team.py`)."""
    _require(_nonempty(team.name), "team.name: empty -- team-header h1")
    _require(_nonempty(team.country), "team.country: empty -- team-header-country")
    _require(_nonempty(team.region), "team.region: empty -- `rating-txt` selector (app/services/team.py)")
    # `tag` silently defaults to "" when the h2 is missing (app/services/team.py).
    _require(_nonempty(team.tag), "team.tag: empty -- team-header h2 selector (app/services/team.py)")

    # 2, not 3: a team mid-roster-transition legitimately drops below a full five,
    # but the roster block vanishing entirely is a selector break.
    _require_min_len(team.roster, 2, "team.roster", "`team-roster-item` selector")
    _require_ratio(team.roster, lambda p: _nonempty(p.alias), 1.0, "team.roster[].alias", "`team-roster-item-name`")
    _require_ratio(team.roster, lambda p: _nonempty(p.id), 1.0, "team.roster[].id", "`team-roster-item` link href")

    _require_min_len(team.completed, 5, "team.completed", "`m-item-result` selector")
    _require_ratio(
        team.completed,
        lambda m: bool(SCORE_OR_FORFEIT_RE.match(m.score or "")),
        0.9,
        "team.completed[].score",
        "`m-item-result` selector",
    )
    _require_ratio(
        team.completed,
        lambda m: _nonempty(m.event) and _nonempty(m.opponent),
        1.0,
        "team.completed[].event/opponent",
        "`m-item-event` selector",
    )
    # `upcoming` is deliberately unchecked for length: an off-season team has none.
    if team.upcoming:
        _require_ratio(
            team.upcoming,
            lambda m: _nonempty(m.event) and _nonempty(m.opponent),
            1.0,
            "team.upcoming[].event/opponent",
            "`m-item-event` selector",
        )


def check_team_ranks(teams: Sequence[Any], threshold: float = 0.75) -> None:
    """At least `threshold` of a sample of top-tier teams must have rank > 0.

    A single team can legitimately lose its rank (inactive roster). All of them
    reporting 0 means the `rank-num mod-` selector stopped matching -- note it is
    a full-attribute match on a compound class, so a rename to `rank-num mod-x`
    silently yields 0 (app/services/team.py).
    """
    _require_ratio(
        teams,
        lambda t: t.rank > 0,
        threshold,
        "team.rank",
        "`rank-num mod-` selector (app/services/team.py)",
    )


def check_rankings(rankings: Sequence[Any]) -> None:
    """Invariants for the rankings page (`app/services/rankings.py`).

    The worst failure mode in the codebase: the `zx-tab` selector missing yields
    `[]` with no error, so an empty list is itself the alarm.
    """
    _require_min_len(rankings, 5, "rankings", "`zx-tab` region selector (app/services/rankings.py)")
    _require_ratio(rankings, lambda r: _nonempty(r.region), 1.0, "rankings[].region", "`zx-tab` region selector")
    _require_ratio(
        rankings,
        lambda r: len(r.teams) >= 10,
        0.8,
        "rankings[].teams",
        "`rank-item` selector",
    )

    all_teams = [team for ranking in rankings for team in ranking.teams]
    _require_ratio(all_teams, lambda t: t.rank > 0, 1.0, "rankings[].teams[].rank", "`rank-item-rank` selector")
    # 90% not 100%: a newly-added team can genuinely sit at 0 points.
    _require_ratio(all_teams, lambda t: t.points > 0, 0.9, "rankings[].teams[].points", "`rank-item-rating` selector")
    _require_ratio(all_teams, lambda t: _nonempty(t.name), 1.0, "rankings[].teams[].name", "`rank-item-team` selector")


def check_player(player: Any) -> None:
    """Invariants for a player page (`app/services/player.py`)."""
    _require(_nonempty(player.name), "player.name: empty -- `wf-title` selector")
    _require(_nonempty(player.alias), "player.alias: empty -- `wf-title` selector")
    _require(_nonempty(player.country), "player.country: empty -- `ge-text-light` selector")

    _require_min_len(player.agents, 1, "player.agents", "agent stats table selector")
    _require_ratio(player.agents, lambda a: _nonempty(a.name), 1.0, "player.agents[].name", "agent stats table")
    # VLR genuinely reports rating 0.00 on agents with a tiny sample, so rating is
    # only checked on the player's main agents. ACS is populated regardless, so it
    # is checked across all of them: both zeroing means the stat columns moved.
    # Every numeric stat routes through `clean_number_string`, which returns 0 on any
    # parse failure, so a shifted or renamed stat column zeroes a whole field with no
    # error. The columns are checked in two groups because VLR does not compute the
    # derived stats -- rating, ADR, KAST -- for agents with a tiny sample, and reports
    # a genuine 0.00 for them (verified on players 45, 3520 and 4521: the zeros start
    # around count<5 and are real data, not a parse failure).
    main_agents = [agent for agent in player.agents if agent.count >= 5]
    if main_agents:
        for field, label in (("rating", "rating"), ("adr", "ADR"), ("kast", "KAST")):
            _require_ratio(
                main_agents,
                lambda a, f=field: getattr(a, f) > 0,
                0.9,
                f"player.agents[].{field}",
                f"agent stats table {label} column",
            )
    # These are raw counts, populated regardless of sample size.
    _require_ratio(player.agents, lambda a: a.acs > 0, 0.9, "player.agents[].acs", "agent stats table ACS column")
    _require_ratio(player.agents, lambda a: a.rounds > 0, 1.0, "player.agents[].rounds", "agent stats rounds column")
    _require_ratio(player.agents, lambda a: a.k > 0, 0.9, "player.agents[].k", "agent stats kills column")

    # `past_teams` is populated by matching the literal heading "past teams", so a
    # retitle silently leaves it []. Checked on established players only: it is the
    # one team field that cannot legitimately be empty, unlike `current_team` (free
    # agents) or `total_winnings` (genuinely 0 for many players).
    _require_min_len(player.past_teams, 1, "player.past_teams", "'past teams' heading match (app/services/player.py)")
    _require_ratio(
        player.past_teams,
        lambda t: _nonempty(t.name) and _nonempty(t.id),
        1.0,
        "player.past_teams[].name/id",
        "'past teams' heading match (app/services/player.py)",
    )

    _require_min_len(player.matches, 5, "player.matches", "`m-item` selector")
    _require_ratio(
        player.matches,
        lambda m: bool(SCORE_OR_FORFEIT_RE.match(m.score or "")),
        0.9,
        "player.matches[].score",
        "`m-item-result` selector",
    )
    _require_ratio(
        player.matches,
        lambda m: _nonempty(m.event) and _nonempty(m.team) and _nonempty(m.opponent),
        1.0,
        "player.matches[].event/team/opponent",
        "`m-item-event` selector",
    )
    # `current_team` and `total_winnings` are deliberately unchecked: players go
    # free-agent and winnings legitimately default to 0.


def check_match_list(matches: Sequence[Any], completed_status: Any = None) -> None:
    """Invariants for the match list (`app/services/matches.py`)."""
    _require_min_len(matches, 10, "matches", "`wf-module-item` selector")
    _require_ratio(matches, lambda m: _nonempty(m.id), 1.0, "matches[].id", "match link href")
    _require_ratio(matches, lambda m: _nonempty(m.event), 1.0, "matches[].event", "`match-item-event` selector")
    # 80%: bracket placeholders are legitimately TBD.
    _require_ratio(
        matches,
        lambda m: m.team1.name != "TBD" and m.team2.name != "TBD",
        0.8,
        "matches[].team1/team2.name",
        "`match-item-vs-team-name` selector",
    )

    # `parse_score` returns None for any non-digit text, so a score-cell rename
    # nulls every score silently. Checked only on completed matches, since an
    # upcoming match legitimately has none -- and only when some are present, as
    # an all-upcoming feed is a valid quiet day.
    if completed_status is not None:
        completed = [m for m in matches if m.status == completed_status]
        if completed:
            _require_ratio(
                completed,
                lambda m: m.team1.score is not None and m.team2.score is not None,
                0.9,
                "matches[].team1/team2.score",
                "`match-item-vs-team-score` selector (app/services/matches.py parse_score)",
            )


def check_match_details(match: Any) -> None:
    """Invariants for a completed match (`app/services/matches.py`).

    Pin this to a completed match from a finished event: its result is frozen
    forever, so any drift is a parser break rather than churn.
    """
    _require(len(match.teams) == 2, f"match.teams: got {len(match.teams)}, expected 2 -- `wf-title-med` selector")
    _require_ratio(match.teams, lambda t: _nonempty(t.name), 1.0, "match.teams[].name", "`wf-title-med` selector")
    # The regression this suite was built for: `js-spoiler` was renamed to
    # `sp-hide` and every completed match silently reported score=None.
    _require_ratio(
        match.teams,
        lambda t: t.score is not None,
        1.0,
        "match.teams[].score",
        "`sp-hide` score selector (app/services/matches.py)",
    )

    _require(_nonempty(match.event.series), "match.event.series: empty -- `match-header-event` selector")
    _require(match.map_count >= 1, f"match.map_count: got {match.map_count} -- map selector")
    _require_min_len(match.data, 1, "match.data", "`vm-stats-game` selector")

    members = [member for map_data in match.data for member in map_data.members]
    _require_min_len(members, 10, "match.data[].members", "scoreboard `tbody` selector")
    _require_ratio(members, lambda m: _nonempty(m.name), 1.0, "match.data[].members[].name", "scoreboard selector")
    _require_ratio(members, lambda m: bool(m.agents), 0.9, "match.data[].members[].agents", "scoreboard agent selector")
    # 90%: a player can genuinely post 0.00 rating on a stomped map.
    _require_ratio(members, lambda m: m.rating > 0, 0.9, "match.data[].members[].rating", "scoreboard stat columns")
    _require_ratio(
        members,
        lambda m: m.kills + m.deaths > 0,
        0.9,
        "match.data[].members[].kills/deaths",
        "scoreboard stat columns",
    )
