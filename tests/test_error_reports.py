import importlib
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.constants import ERROR_REPORT_MAX_BODY_BYTES, ERROR_REPORT_MAX_TRACE_BYTES
from app.core.config import settings

API_KEY = "mobile-key"
SOURCE = "mobile"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def _client(monkeypatch, *, dsn: str | None = "https://public@sentry.invalid/1") -> TestClient:
    """Build an app with the error-report router mounted per the current settings."""
    monkeypatch.setattr(settings, "SENTRY_DSN", dsn)
    monkeypatch.setattr(settings, "API_KEYS", {SOURCE: API_KEY})

    import app.api.v1.api as api_module

    importlib.reload(api_module)
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_redis_client] = lambda: None
    return TestClient(app)


@contextmanager
def _sentry(captured: list[dict[str, Any]], *, active: bool = True):
    """Stand in for the Sentry SDK at the endpoint's boundary, recording captured events."""
    client = MagicMock()
    client.is_active.return_value = active
    client.capture_event.side_effect = lambda event, scope=None: captured.append(event) or event["event_id"]

    sdk = MagicMock()
    sdk.get_client.return_value = client
    with patch("app.api.v1.endpoints.error_reports.sentry_sdk", sdk):
        yield


def _report(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_event_id": str(uuid.uuid4()),
        "occurred_at": "2026-02-01T10:00:00+00:00",
        "platform": "android",
        "app_version": "1.4.2",
        "app_build": "142",
        "error_kind": "http",
        "error_type": "HttpException",
        "error_message": "Request to /matches failed",
        "screen": "MatchesScreen",
        "request_method": "GET",
        "request_path": "/api/v1/matches",
        "response_status": 500,
        "backend_request_id": "req-42",
    }
    payload.update(overrides)
    return payload


def test_report_is_captured_as_a_mobile_originated_event(monkeypatch):
    """A report about a backend 500 is accepted and reaches Sentry tagged as a client report."""
    captured: list[dict[str, Any]] = []
    payload = _report()

    with _sentry(captured):
        response = _client(monkeypatch).post("/api/v1/error-reports/", json=payload, headers=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == payload["client_event_id"]
    assert datetime.fromisoformat(body["received_at"]).tzinfo is not None

    (event,) = captured
    assert event["event_id"] == uuid.UUID(payload["client_event_id"]).hex
    # The decipherability contract: never confusable with, and never grouped with, backend events.
    assert event["tags"]["error_origin"] == "mobile-app"
    assert event["fingerprint"][0] == "mobile-error-report"
    assert event["tags"]["api.status"] == "500"
    assert event["tags"]["report.source"] == SOURCE
    assert event["tags"]["backend_request_id"] == "req-42"
    assert event["contexts"]["error_report"]["screen"] == "MatchesScreen"


def test_retrying_the_same_report_is_idempotent(monkeypatch):
    """A client retry reuses its event id, so the response and the Sentry event id stay stable."""
    captured: list[dict[str, Any]] = []
    payload = _report()

    with _sentry(captured):
        client = _client(monkeypatch)
        first = client.post("/api/v1/error-reports/", json=payload, headers=AUTH)
        second = client.post("/api/v1/error-reports/", json=payload, headers=AUTH)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] == second.json()["id"] == payload["client_event_id"]
    assert captured[0]["event_id"] == captured[1]["event_id"]


def test_oversized_stack_trace_is_truncated_and_flagged(monkeypatch):
    """The server caps blobs itself, so a client cannot claim an untruncated oversized trace."""
    captured: list[dict[str, Any]] = []
    payload = _report(stack_trace="x" * (ERROR_REPORT_MAX_TRACE_BYTES + 500), trace_truncated=False)

    with _sentry(captured):
        response = _client(monkeypatch).post("/api/v1/error-reports/", json=payload, headers=AUTH)

    assert response.status_code == 201
    context = captured[0]["contexts"]["error_report"]
    assert len(context["stack_trace"].encode("utf-8")) == ERROR_REPORT_MAX_TRACE_BYTES
    assert context["trace_truncated"] is True
    assert captured[0]["extra"]["stack_trace"] == context["stack_trace"]


def test_credentials_never_cross_the_sentry_boundary(monkeypatch):
    """Client text is redacted and unknown query params dropped before anything leaves the app."""
    captured: list[dict[str, Any]] = []
    payload = _report(
        error_message="401 for Bearer sk-secret-token-value",
        request_query={"page": "2", "access_token": "sk-secret-token-value"},
    )

    with _sentry(captured):
        response = _client(monkeypatch).post("/api/v1/error-reports/", json=payload, headers=AUTH)

    assert response.status_code == 201
    event = captured[0]
    assert "sk-secret-token-value" not in event["message"]
    assert "[REDACTED]" in event["message"]
    context = event["contexts"]["error_report"]
    assert "sk-secret-token-value" not in context["error_message"]
    assert context["request_query"] == {"page": "2"}


def test_report_without_a_valid_key_is_rejected(monkeypatch):
    """Ingestion is authenticated even though the rest of the API can run open."""
    captured: list[dict[str, Any]] = []

    with _sentry(captured):
        response = _client(monkeypatch).post(
            "/api/v1/error-reports/", json=_report(), headers={"Authorization": "Bearer wrong"}
        )

    assert response.status_code == 401
    assert captured == []


def test_unavailable_sentry_client_returns_503(monkeypatch):
    """A report that cannot be captured must not be acknowledged as stored."""
    captured: list[dict[str, Any]] = []

    with _sentry(captured, active=False):
        response = _client(monkeypatch).post("/api/v1/error-reports/", json=_report(), headers=AUTH)

    assert response.status_code == 503
    assert captured == []


def test_oversized_body_is_rejected_before_parsing(monkeypatch):
    """Bodies past the ingestion cap are refused with 413 rather than parsed and captured."""
    captured: list[dict[str, Any]] = []
    payload = _report(stack_trace="x" * (ERROR_REPORT_MAX_BODY_BYTES + 1000))

    with _sentry(captured):
        response = _client(monkeypatch).post("/api/v1/error-reports/", json=payload, headers=AUTH)

    assert response.status_code == 413
    assert captured == []


def test_before_send_strips_the_ingestion_request_from_forwarded_reports():
    """The SDK attaches the ingestion POST's raw body to captured events, bypassing redaction.

    before_send must drop it and relabel the transaction with what the client observed,
    while leaving the backend's own events untouched.
    """
    from sentry_sdk.types import Event

    from app.utils import before_send

    mobile_event: Event = {
        "tags": {"error_origin": "mobile-app", "api.path": "/api/v1/matches"},
        "request": {"data": '{"error_message": "Bearer sk-secret"}'},
        "transaction": "/",
    }
    sanitized = before_send(mobile_event, {})
    assert sanitized is not None
    assert "request" not in sanitized
    assert sanitized["transaction"] == "/api/v1/matches"

    backend_event: Event = {"request": {"data": "backend"}, "transaction": "/api/v1/team/1"}
    assert before_send(backend_event, {}) == backend_event


def test_endpoint_is_absent_without_a_sentry_dsn(monkeypatch):
    """Without a DSN there is nowhere to forward reports, so the route is not mounted."""
    response = _client(monkeypatch, dsn=None).post("/api/v1/error-reports/", json=_report(), headers=AUTH)

    assert response.status_code == 404
