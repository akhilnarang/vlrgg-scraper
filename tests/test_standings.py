from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import standings


@pytest.mark.asyncio
async def test_standings_follow_the_public_response_contract():
    response = AsyncMock(
        status_code=200,
        content=(Path(__file__).parent / "fixtures" / "standings_2021.html").read_bytes(),
    )

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await standings.standings_list(2021)

    assert result.year == 2021
    assert [circuit.region for circuit in result.circuits] == ["North America Circuit", "EMEA Circuit"]
    assert [(team.name, team.rank, team.points) for team in result.circuits[0].teams] == [
        ("Sentinels", 1, 775),
        ("ENVY", 2, 450),
    ]
