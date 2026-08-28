from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import rankings


@pytest.mark.asyncio
async def test_rankings_follow_the_public_response_contract():
    response = AsyncMock(status_code=200, content=(Path(__file__).parent / "fixtures" / "rankings.html").read_bytes())

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await rankings.ranking_list()

    assert result[0].region == "Na"
    assert [(team.name, team.rank, team.points) for team in result[0].teams[:1]] == [("Sentinels", 1, 775)]
