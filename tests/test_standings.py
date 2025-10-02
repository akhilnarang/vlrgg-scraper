import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.services import standings


@pytest.mark.asyncio
async def test_standings_list():
    # Load the fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "standings_2021.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await standings.standings_list(2021)

    # Assertions
    assert result.year == 2021
    assert len(result.circuits) == 2

    # Check North America Circuit
    na_circuit = result.circuits[0]
    assert na_circuit.region == "North America Circuit"
    assert len(na_circuit.teams) == 2

    sentinels = na_circuit.teams[0]
    assert sentinels.name == "Sentinels"
    assert sentinels.id == 2
    assert str(sentinels.logo) == "https://www.vlr.gg/img/62875027c8e06.png"
    assert sentinels.rank == 1
    assert sentinels.points == 775
    assert sentinels.country == "United States"

    envy = na_circuit.teams[1]
    assert envy.name == "ENVY"
    assert envy.id == 427
    assert envy.rank == 2
    assert envy.points == 450

    # Check EMEA Circuit
    emea_circuit = result.circuits[1]
    assert emea_circuit.region == "EMEA Circuit"
    assert len(emea_circuit.teams) == 1

    acend = emea_circuit.teams[0]
    assert acend.name == "Acend"
    assert acend.id == 3531
    assert acend.rank == 1
    assert acend.points == 375
    assert acend.country == "Europe"
