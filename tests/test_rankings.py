import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.services import rankings


@pytest.mark.asyncio
async def test_ranking_list():
    # Load the fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "rankings.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await rankings.ranking_list()

    # Assertions
    assert len(result) > 0

    # Check first region
    na_region = result[0]
    assert na_region.region == "Na"
    assert len(na_region.teams) > 0

    # Check first team
    sentinels = na_region.teams[0]
    assert sentinels.name == "Sentinels"
    assert sentinels.id == 2
    assert sentinels.rank == 1
    assert sentinels.points == 775
    assert sentinels.country == "United States"


@pytest.mark.asyncio
async def test_ranking_list_current():
    # Load the current fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "rankings_current.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await rankings.ranking_list()

    # Assertions
    assert len(result) == 12

    # Check some regions
    regions = [r.region for r in result]
    assert "North America" in regions
    assert "Europe" in regions
    assert "Brazil" in regions
