"""Mutation tests for the live parser contracts.

Every contract is fed known-broken output and must raise, and known-good output
and must not. A contract that cannot fail is worse than no contract: it is a
green light wired to nothing, and the daily job that depends on it is a no-op.

These run offline, in normal CI, on every PR. They are tests of the tests.
"""

from types import SimpleNamespace

import pytest

from tests.live import contracts
from tests.live.contracts import ContractViolation


def _player(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**(dict(id="1", alias="tenz", name="Tyson") | overrides))


def _completed(score: str = "2:1", **overrides) -> SimpleNamespace:
    base = dict(id="1", event="VCT 26", stage="GF", opponent="NRG", score=score)
    return SimpleNamespace(**(base | overrides))


def _team(**overrides) -> SimpleNamespace:
    base = dict(
        name="Sentinels",
        tag="SEN",
        country="United States",
        rank=3,
        region="North America",
        roster=[_player() for _ in range(5)],
        upcoming=[],
        completed=[_completed() for _ in range(10)],
    )
    return SimpleNamespace(**(base | overrides))


def _ranked_team(**overrides) -> SimpleNamespace:
    base = dict(name="Sentinels", id=2, rank=3, points=1500, country="United States")
    return SimpleNamespace(**(base | overrides))


def _ranking(**overrides) -> SimpleNamespace:
    base = dict(region="North America", teams=[_ranked_team() for _ in range(12)])
    return SimpleNamespace(**(base | overrides))


def _agent(**overrides) -> SimpleNamespace:
    base = dict(name="jett", count=50, rating=1.2, acs=250.0, rounds=500, adr=150.0, kast=70.0, k=400)
    return SimpleNamespace(**(base | overrides))


def _past_team(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**(dict(id="1", name="Cloud9") | overrides))


def _player_match(score: str = "2:0", **overrides) -> SimpleNamespace:
    base = dict(id="1", event="VCT 26", stage="GF", team="SEN", opponent="NRG", score=score)
    return SimpleNamespace(**(base | overrides))


def _player_page(**overrides) -> SimpleNamespace:
    base = dict(
        name="Tyson Ngo",
        alias="TenZ",
        country="Canada",
        agents=[_agent() for _ in range(3)],
        matches=[_player_match() for _ in range(10)],
        past_teams=[_past_team() for _ in range(2)],
        current_team=None,
        total_winnings=0.0,
    )
    return SimpleNamespace(**(base | overrides))


def _listed_match(name1: str = "SEN", name2: str = "NRG", **overrides) -> SimpleNamespace:
    # Mirrors app.schemas.matches.Match: team1/team2, not a `teams` list.
    base = dict(
        id="1",
        event="VCT 26",
        status="completed",
        team1=SimpleNamespace(name=name1, score=2),
        team2=SimpleNamespace(name=name2, score=1),
    )
    return SimpleNamespace(**(base | overrides))


def _member(**overrides) -> SimpleNamespace:
    base = dict(name="tenz", agents=[SimpleNamespace(title="jett")], rating=1.2, kills=20, deaths=14)
    return SimpleNamespace(**(base | overrides))


def _map_data(members=None, **overrides) -> SimpleNamespace:
    base = dict(map="Ascent", members=members if members is not None else [_member() for _ in range(10)])
    return SimpleNamespace(**(base | overrides))


def _match_details(**overrides) -> SimpleNamespace:
    base = dict(
        teams=[SimpleNamespace(name="NRG", score=3), SimpleNamespace(name="FNATIC", score=2)],
        event=SimpleNamespace(series="VCT 25", stage="GF"),
        map_count=5,
        data=[_map_data()],
    )
    return SimpleNamespace(**(base | overrides))


# --- healthy data must pass -------------------------------------------------
# Guards against contracts so strict they fire on good data and get muted.


def test_healthy_team_passes():
    contracts.check_team(_team())


def test_healthy_team_ranks_pass():
    contracts.check_team_ranks([_team() for _ in range(4)])


def test_healthy_rankings_pass():
    contracts.check_rankings([_ranking() for _ in range(10)])


def test_healthy_player_passes():
    contracts.check_player(_player_page())


def test_healthy_match_list_passes():
    contracts.check_match_list([_listed_match() for _ in range(15)])


def test_healthy_match_details_pass():
    contracts.check_match_details(_match_details())


def test_team_with_no_upcoming_passes():
    """An off-season team legitimately has zero upcoming matches."""
    contracts.check_team(_team(upcoming=[]))


def test_player_without_current_team_passes():
    """A free-agent player legitimately has no current team."""
    contracts.check_player(_player_page(current_team=None, total_winnings=0.0))


def test_player_with_unrated_niche_agents_passes():
    """VLR reports rating 0.00 on agents with a tiny sample size.

    Modelled on real output for player 3520 (ZmjjKK): 19 agents, of which 11 played
    fewer than 5 times carry rating 0.00 while ACS is populated throughout. Only
    42% of agents rated, and none of it is a parser fault -- so rating is checked
    on main agents alone.
    """
    agents = [_agent(name=f"main{i}", count=50, rating=1.1) for i in range(8)] + [
        _agent(name=f"niche{i}", count=1, rating=0.0, adr=0.0, kast=0.0, acs=180.0) for i in range(11)
    ]
    contracts.check_player(_player_page(agents=agents))


def test_team_with_two_player_roster_passes():
    """A roster mid-transition legitimately drops below a full five."""
    contracts.check_team(_team(roster=[_player() for _ in range(2)]))


# --- broken data must fire --------------------------------------------------


@pytest.mark.parametrize(
    ("case", "broken"),
    [
        ("rank_zeroed", lambda: _team(rank=0)),
        ("region_empty", lambda: _team(region="")),
        ("country_empty", lambda: _team(country="")),
        ("name_empty", lambda: _team(name="")),
        ("tag_empty", lambda: _team(tag="")),
        ("roster_empty", lambda: _team(roster=[])),
        ("roster_aliases_empty", lambda: _team(roster=[_player(alias="") for _ in range(5)])),
        ("roster_ids_empty", lambda: _team(roster=[_player(id="") for _ in range(5)])),
        ("completed_empty", lambda: _team(completed=[])),
        ("all_scores_empty", lambda: _team(completed=[_completed(score="") for _ in range(10)])),
        ("all_events_empty", lambda: _team(completed=[_completed(event="") for _ in range(10)])),
    ],
)
def test_check_team_fires(case, broken):
    # rank is checked by check_team_ranks, not check_team.
    contract = contracts.check_team_ranks if case == "rank_zeroed" else contracts.check_team
    target = [broken()] if case == "rank_zeroed" else broken()
    with pytest.raises(ContractViolation):
        contract(target)


@pytest.mark.parametrize(
    ("case", "broken"),
    [
        # rankings.py returning [] when the `zx-tab` selector misses.
        ("all_regions_gone", lambda: []),
        ("too_few_regions", lambda: [_ranking() for _ in range(2)]),
        ("regions_empty_string", lambda: [_ranking(region="") for _ in range(10)]),
        ("all_teams_empty", lambda: [_ranking(teams=[]) for _ in range(10)]),
        ("ranks_zeroed", lambda: [_ranking(teams=[_ranked_team(rank=0) for _ in range(12)]) for _ in range(10)]),
        ("points_zeroed", lambda: [_ranking(teams=[_ranked_team(points=0) for _ in range(12)]) for _ in range(10)]),
        ("names_empty", lambda: [_ranking(teams=[_ranked_team(name="") for _ in range(12)]) for _ in range(10)]),
    ],
)
def test_check_rankings_fires(case, broken):
    with pytest.raises(ContractViolation):
        contracts.check_rankings(broken())


@pytest.mark.parametrize(
    ("case", "broken"),
    [
        ("name_empty", lambda: _player_page(name="")),
        ("country_empty", lambda: _player_page(country="")),
        ("agents_empty", lambda: _player_page(agents=[])),
        ("main_agent_ratings_zeroed", lambda: _player_page(agents=[_agent(rating=0.0) for _ in range(3)])),
        ("agent_acs_zeroed", lambda: _player_page(agents=[_agent(acs=0.0) for _ in range(3)])),
        ("agent_adr_zeroed", lambda: _player_page(agents=[_agent(adr=0.0) for _ in range(3)])),
        ("agent_kast_zeroed", lambda: _player_page(agents=[_agent(kast=0.0) for _ in range(3)])),
        ("agent_rounds_zeroed", lambda: _player_page(agents=[_agent(rounds=0) for _ in range(3)])),
        ("agent_kills_zeroed", lambda: _player_page(agents=[_agent(k=0) for _ in range(3)])),
        ("past_teams_empty", lambda: _player_page(past_teams=[])),
        ("past_team_names_empty", lambda: _player_page(past_teams=[_past_team(name="") for _ in range(2)])),
        ("matches_empty", lambda: _player_page(matches=[])),
        ("match_scores_empty", lambda: _player_page(matches=[_player_match(score="") for _ in range(10)])),
        ("match_events_empty", lambda: _player_page(matches=[_player_match(event="") for _ in range(10)])),
    ],
)
def test_check_player_fires(case, broken):
    with pytest.raises(ContractViolation):
        contracts.check_player(broken())


@pytest.mark.parametrize(
    ("case", "broken"),
    [
        ("empty", lambda: []),
        ("too_few", lambda: [_listed_match() for _ in range(3)]),
        ("ids_empty", lambda: [_listed_match(id="") for _ in range(15)]),
        ("events_empty", lambda: [_listed_match(event="") for _ in range(15)]),
        ("all_teams_tbd", lambda: [_listed_match(name1="TBD", name2="TBD") for _ in range(15)]),
    ],
)
def test_check_match_list_fires(case, broken):
    with pytest.raises(ContractViolation):
        contracts.check_match_list(broken())


@pytest.mark.parametrize(
    ("case", "broken"),
    [
        # The `js-spoiler` -> `sp-hide` rename this suite was built for.
        (
            "scores_none",
            lambda: _match_details(
                teams=[SimpleNamespace(name="NRG", score=None), SimpleNamespace(name="FNATIC", score=None)]
            ),
        ),
        ("data_empty", lambda: _match_details(data=[])),
        ("members_empty", lambda: _match_details(data=[_map_data(members=[])])),
        ("ratings_zeroed", lambda: _match_details(data=[_map_data(members=[_member(rating=0.0) for _ in range(10)])])),
        (
            "stats_zeroed",
            lambda: _match_details(data=[_map_data(members=[_member(kills=0, deaths=0) for _ in range(10)])]),
        ),
        ("member_names_empty", lambda: _match_details(data=[_map_data(members=[_member(name="") for _ in range(10)])])),
        ("event_series_empty", lambda: _match_details(event=SimpleNamespace(series="", stage="GF"))),
    ],
)
def test_check_match_details_fires(case, broken):
    with pytest.raises(ContractViolation):
        contracts.check_match_details(broken())


# --- threshold boundaries ---------------------------------------------------
# Without these, a typo'd threshold (0.075 for 0.75) sails through unnoticed.


def test_team_ranks_tolerates_one_unranked_team():
    """3 of 4 ranked = 75%, exactly at threshold: must not fire."""
    teams = [_team(rank=0)] + [_team(rank=5) for _ in range(3)]
    contracts.check_team_ranks(teams)


def test_team_ranks_fires_below_threshold():
    """2 of 4 ranked = 50%, below threshold: must fire."""
    teams = [_team(rank=0), _team(rank=0), _team(rank=5), _team(rank=5)]
    with pytest.raises(ContractViolation):
        contracts.check_team_ranks(teams)


def test_team_scores_tolerate_one_odd_card():
    """9 of 10 parseable = 90%, exactly at threshold: must not fire."""
    completed = [_completed(score="")] + [_completed() for _ in range(9)]
    contracts.check_team(_team(completed=completed))


def test_team_scores_fire_below_threshold():
    completed = [_completed(score="") for _ in range(2)] + [_completed() for _ in range(8)]
    with pytest.raises(ContractViolation):
        contracts.check_team(_team(completed=completed))


def test_forfeit_scores_are_accepted():
    """VLR shows forfeits instead of a numeric score; that is not a break."""
    contracts.check_team(_team(completed=[_completed(score="FF") for _ in range(10)]))


def test_match_list_tolerates_some_tbd_brackets():
    """3 of 15 TBD = 80% named, exactly at threshold: must not fire."""
    matches = [_listed_match(name1="TBD", name2="TBD") for _ in range(3)] + [_listed_match() for _ in range(12)]
    contracts.check_match_list(matches)


def test_violation_message_names_the_selector():
    """Failure messages must point at the suspect selector to be actionable."""
    with pytest.raises(ContractViolation, match="sp-hide"):
        contracts.check_match_details(
            _match_details(teams=[SimpleNamespace(name="A", score=None), SimpleNamespace(name="B", score=None)])
        )
