"""Verify contracts against real pydantic models, not duck-typed stand-ins.

The sensitivity suite builds `SimpleNamespace` fixtures, which is what makes the
mutations cheap to write -- but it means a contract can reference a field the real
schema does not have and still pass. That happened: `check_match_list` read
`m.teams[0]` when `app.schemas.matches.Match` defines `team1`/`team2`, and only a
live run against VLR caught it.

These tests feed healthy instances of the *actual* response models through every
contract. They run offline in normal CI, so a schema rename or a typo'd field
fails in the PR rather than at 03:00 UTC.
"""

from datetime import UTC, datetime

import pytest

from app import schemas
from app.constants import EventStatus, MatchStatus, SearchCategory
from tests.live import contracts


IMG = "https://owcdn.net/img/x.png"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _team_model() -> schemas.Team:
    return schemas.Team(
        name="Sentinels",
        tag="SEN",
        img=IMG,
        website="https://sentinels.gg/",
        twitter="https://x.com/Sentinels",
        country="United States",
        rank=3,
        region="North America",
        roster=[schemas.team.Player(id=str(i), alias=f"p{i}", img=IMG) for i in range(5)],
        upcoming=[],
        completed=[
            schemas.team.CompletedMatch(id=str(i), event="VCT 26", stage="GF", opponent="NRG", date=NOW, score="2:1")
            for i in range(10)
        ],
    )


def _ranking_model() -> schemas.Ranking:
    return schemas.Ranking(
        region="North America",
        teams=[
            schemas.rankings.TeamRanking(name=f"t{i}", id=i + 1, logo=IMG, rank=i + 1, points=1500, country="US")
            for i in range(12)
        ],
    )


def _player_model() -> schemas.Player:
    agent = schemas.player.Agent(
        name="jett",
        img=IMG,
        count=50,
        percent=0.5,
        rounds=500,
        rating=1.2,
        acs=250.0,
        kd=1.3,
        adr=150.0,
        kast=70.0,
        kpr=0.9,
        apr=0.3,
        fkpr=0.2,
        fdpr=0.1,
        k=400,
        d=300,
        a=100,
        fk=50,
        fd=30,
    )
    return schemas.Player(
        name="Tyson Ngo",
        alias="TenZ",
        country="Canada",
        img=IMG,
        twitter="https://x.com/TenZ",
        agents=[agent],
        past_teams=[schemas.player.PlayerTeam(id="1", name="Cloud9", img=IMG)],
        matches=[
            schemas.player.PlayerMatch(
                id=str(i), date=NOW, event="VCT 26", stage="GF", team="SEN", opponent="NRG", score="2:0"
            )
            for i in range(10)
        ],
    )


def _match_list_model() -> list[schemas.Match]:
    return [
        schemas.Match(
            id=str(i),
            team1=schemas.matches.MatchTeam(name="SEN", score=2),
            team2=schemas.matches.MatchTeam(name="NRG", score=1),
            status=MatchStatus.COMPLETED,
            time=NOW,
            event="VCT 26",
            series="Playoffs",
        )
        for i in range(15)
    ]


def _match_details_model() -> schemas.MatchWithDetails:
    member = schemas.matches.TeamMember(
        id="1",
        name="tenz",
        team="SEN",
        agents=[schemas.matches.Agent(title="jett", img=IMG)],
        rating=1.2,
        acs=250,
        kills=20,
        deaths=14,
        assists=5,
        kast=70,
        adr=150,
        headshot_percent=25,
        first_kills=3,
        first_deaths=2,
        first_kills_diff=1,
    )
    return schemas.MatchWithDetails(
        teams=[
            schemas.matches.TeamWithImage(name="NRG", score=3, img=IMG),
            schemas.matches.TeamWithImage(name="FNATIC", score=2, img=IMG),
        ],
        bans=[],
        event=schemas.matches.Event(id="1", img=IMG, series="VCT 25", stage="GF"),
        videos=schemas.matches.MatchVideos(streams=[], vods=[]),
        map_count=5,
        data=[
            schemas.matches.MatchData(
                map="Ascent",
                teams=[schemas.matches.Team(name="NRG", score=13), schemas.matches.Team(name="FNATIC", score=11)],
                members=[member for _ in range(10)],
                rounds=[
                    schemas.matches.Round(
                        round_number=1, round_score="1-0", winner="NRG", side="attack", win_type="elim"
                    )
                ],
            )
        ],
        previous_encounters=[],
    )


def _search_model() -> list[schemas.SearchResult]:
    return [
        schemas.SearchResult(id=str(i), name=f"Team {i}", img=IMG, category=SearchCategory.TEAM, description="desc")
        for i in range(8)
    ]


def _event_list_model() -> list[schemas.Event]:
    return [
        schemas.Event(
            id=str(i),
            title=f"VCT 26: Stage {i}",
            status=EventStatus.COMPLETED,
            prize="$250,000",
            dates="Jul 1 - Jul 20",
            location="Los Angeles",
            img=IMG,
        )
        for i in range(15)
    ]


def _news_list_model() -> list[schemas.NewsItem]:
    return [
        schemas.NewsItem(
            url=f"https://vlr.gg/{i}", title=f"Story {i}", description="A blurb.", date=NOW, author="editor"
        )
        for i in range(15)
    ]


def test_check_search_results_matches_schema():
    contracts.check_search_results(_search_model())


def test_check_event_list_matches_schema():
    """`status` is an EventStatus enum, so the contract must read `.value`."""
    contracts.check_event_list(_event_list_model())


def test_check_news_list_matches_schema():
    contracts.check_news_list(_news_list_model())


def test_check_team_socials_matches_schema():
    contracts.check_team_socials([_team_model() for _ in range(4)])


def test_check_player_socials_matches_schema():
    contracts.check_player_socials([_player_model() for _ in range(4)])


def test_check_team_rosters_matches_schema():
    contracts.check_team_rosters([_team_model() for _ in range(4)])


def test_check_team_matches_schema():
    contracts.check_team(_team_model())


def test_check_team_ranks_matches_schema():
    contracts.check_team_ranks([_team_model() for _ in range(4)])


def test_check_rankings_matches_schema():
    contracts.check_rankings([_ranking_model() for _ in range(10)])


def test_check_player_matches_schema():
    contracts.check_player(_player_model())


def test_check_match_list_matches_schema():
    """Would have caught `m.teams[0]` against a schema defining team1/team2."""
    contracts.check_match_list(_match_list_model(), completed_status=MatchStatus.COMPLETED)


def test_check_match_details_matches_schema():
    contracts.check_match_details(_match_details_model())


def test_match_list_score_check_reads_real_fields():
    """A nulled score on real models must fire, not raise AttributeError."""
    matches = _match_list_model()
    for match in matches:
        match.team1.score = None
        match.team2.score = None
    with pytest.raises(contracts.ContractViolation, match="score"):
        contracts.check_match_list(matches, completed_status=MatchStatus.COMPLETED)
