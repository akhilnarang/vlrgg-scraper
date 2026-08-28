import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.runner import run_ask
from app.exceptions import ScrapingError


def _tool_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", call_id="call-1", name=name, arguments=json.dumps(arguments))


def _response(output: list, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(output=output, output_text=text)


@pytest.mark.asyncio
async def test_run_ask_uses_a_tool_then_returns_an_answer(monkeypatch):
    monkeypatch.setattr("app.agent.runner.settings.LLM_DEBUG", True)

    class Responses:
        def __init__(self):
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _response([_tool_call("search", {"category": "teams", "term": "100T"})])
            return _response([SimpleNamespace(type="message")], "100T have no match scheduled.")

    with patch(
        "app.agent.tools._search",
        new=AsyncMock(return_value=[{"id": "120", "name": "100 Thieves", "category": "teams"}]),
    ):
        result = await run_ask("when does 100T play next", None, SimpleNamespace(responses=Responses()))

    assert result.answer == "100T have no match scheduled."
    assert result.tools_used and result.tools_used[-1]["tool"] == "search"


@pytest.mark.asyncio
async def test_run_ask_turns_a_tool_failure_into_an_answer(monkeypatch):
    monkeypatch.setattr("app.agent.runner.settings.LLM_DEBUG", False)

    class Responses:
        def __init__(self):
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _response([_tool_call("get_team", {"id": "624"})])
            return _response([SimpleNamespace(type="message")], "I couldn't fetch that team right now.")

    with patch(
        "app.agent.tools._get_team",
        new=AsyncMock(side_effect=ScrapingError(url="https://vlr.gg/team/624", upstream_status=500)),
    ):
        result = await run_ask("team info", None, SimpleNamespace(responses=Responses()))

    assert result.answer == "I couldn't fetch that team right now."
