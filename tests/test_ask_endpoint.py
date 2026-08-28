import importlib
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas import AskResponse


def _app_with_current_settings() -> FastAPI:
    import app.api.v1.api as api_module

    importlib.reload(api_module)
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/v1")
    return app


def test_ask_answers_when_configured(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", "key", raising=False)
    app = _app_with_current_settings()

    from app.api import deps

    app.dependency_overrides[deps.get_redis_client] = lambda: None
    with (
        patch("app.api.v1.endpoints.ask.run_ask", new=AsyncMock(return_value=AskResponse(answer="42"))),
        patch("app.api.v1.endpoints.ask.enforce_rate_limit", new=AsyncMock(return_value=None)),
    ):
        response = TestClient(app).post("/api/v1/ask", json={"query": "x"})

    assert response.status_code == 200
    assert response.json()["answer"] == "42"


def test_ask_is_unavailable_without_a_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LLM_API_KEY", None, raising=False)

    response = TestClient(_app_with_current_settings()).post("/api/v1/ask", json={"query": "x"})

    assert response.status_code == 404
