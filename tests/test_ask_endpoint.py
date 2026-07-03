import importlib
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.schemas import AskResponse


def _app_with_ask():
    import app.api.v1.api as apimod
    importlib.reload(apimod)
    app = FastAPI()
    app.include_router(apimod.router, prefix="/api/v1")
    return app


def test_ask_route_absent_without_key(monkeypatch):
    monkeypatch.setattr("app.api.v1.api.settings.LLM_API_KEY", None, raising=False)
    app = _app_with_ask()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/ask/" not in paths and not any(p.startswith("/api/v1/ask") for p in paths)


def test_ask_route_present_and_answers_with_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", "k", raising=False)
    app = _app_with_ask()
    # assert the real trailing-slash path is registered
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/ask/" in paths
    # bypass redis dependency
    import app.api.deps as deps
    app.dependency_overrides[deps.get_redis_client] = lambda: None
    with patch("app.api.v1.endpoints.ask.run_ask",
               new=AsyncMock(return_value=AskResponse(answer="42"))), \
         patch("app.api.v1.endpoints.ask.enforce_rate_limit", new=AsyncMock(return_value=None)):
        client = TestClient(app)
        r = client.post("/api/v1/ask", json={"query": "x"})
    assert r.status_code == 200 and r.json()["answer"] == "42"


def test_rate_limit_429_short_circuits_run_ask(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", "k", raising=False)
    app = _app_with_ask()
    import app.api.deps as deps
    app.dependency_overrides[deps.get_redis_client] = lambda: None
    run_mock = AsyncMock()
    with patch("app.api.v1.endpoints.ask.run_ask", new=run_mock), \
         patch("app.api.v1.endpoints.ask.enforce_rate_limit",
               new=AsyncMock(side_effect=HTTPException(status_code=429, headers={"Retry-After": "60"}))):
        client = TestClient(app)
        r = client.post("/api/v1/ask", json={"query": "x"})
    assert r.status_code == 429
    run_mock.assert_not_awaited()
