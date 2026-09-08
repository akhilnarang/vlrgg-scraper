"""Pure helpers for mobile error-report ingestion: redaction and Sentry event construction.

No I/O happens here - the endpoint owns capture/flush. The event shape is the decipherability
contract: every event carries `error_origin=mobile-app` and a `mobile-error-report` fingerprint
namespace, so a report *about* a backend 5xx never groups with the backend's own Sentry events.
"""

import re
from datetime import datetime
from typing import Any

from app.schemas import ErrorReportIn

REDACTED = "[REDACTED]"

# Bearer tokens, then `key: value` style credential material. Applied in that order so a
# redacted bearer value is not re-matched into something less readable.
_BEARER_RE = re.compile(r"(?i)bearer\s+[a-z0-9._~+/-]+=*")
_CREDENTIAL_RE = re.compile(r"""(?i)(api[_-]?key|token|authorization|password|secret)["':=\s]+\S+""")

# Query params safe to keep verbatim; anything else is dropped rather than stored as a placeholder,
# since unknown params are the ones most likely to carry user or credential material.
QUERY_ALLOWLIST = frozenset(
    {"page", "tab", "region", "year", "category", "type", "timespan", "group", "series_id", "tier", "q"}
)


def redact(text: str | None) -> str | None:
    """
    Strip credential material out of free-form client text.

    :param text: The text to redact, if any
    :return: The redacted text, or None if nothing was given
    """
    if text is None:
        return None
    redacted = _BEARER_RE.sub(REDACTED, text)
    return _CREDENTIAL_RE.sub(rf"\1={REDACTED}", redacted)


def _filter_query(query: dict[str, str] | None) -> dict[str, str] | None:
    """
    Keep only allowlisted query parameters.

    :param query: The reported query parameters, if any
    :return: The filtered parameters, or None if nothing was given
    """
    if query is None:
        return None
    return {key: value for key, value in query.items() if key in QUERY_ALLOWLIST}


def build_sentry_event(report: ErrorReportIn, source: str, received_at: datetime) -> dict[str, Any]:
    """
    Build the Sentry event for a mobile error report.

    :param report: The validated (already size-capped) report
    :param source: The authenticated API key source that submitted the report
    :param received_at: When the server accepted the report
    :return: A plain Sentry event dict, ready for `capture_event`
    """
    error_message = redact(report.error_message) or ""
    stack_trace = redact(report.stack_trace)
    response_body = redact(report.response_body)
    request_query = _filter_query(report.request_query)

    tags: dict[str, str] = {
        # The backend's own events never carry this tag, so `error_origin:mobile-app` alone
        # separates client reports from server-side errors.
        "error_origin": "mobile-app",
        "report.source": source,
        "mobile.platform": report.platform,
        "mobile.app_version": report.app_version,
        "mobile.app_build": report.app_build,
        "mobile.error_kind": report.error_kind,
    }
    if report.screen:
        tags["mobile.screen"] = report.screen
    if report.operation:
        tags["mobile.operation"] = report.operation
    if report.request_method:
        tags["api.method"] = report.request_method
    if report.request_path:
        tags["api.path"] = report.request_path
    if report.response_status is not None:
        # `api.status:500` means "the app observed a backend 500", not "the backend raised a 500".
        tags["api.status"] = str(report.response_status)
    if report.backend_request_id:
        # Correlates this report with the backend's own event/logs for the same request.
        tags["backend_request_id"] = report.backend_request_id

    contexts: dict[str, Any] = {}
    device = {
        key: value
        for key, value in (("brand", report.device_brand), ("model", report.device_model))
        if value is not None
    }
    if device:
        contexts["device"] = device

    os_context: dict[str, Any] = {"name": "Android" if report.platform == "android" else "iOS"}
    if report.os_version is not None:
        os_context["version"] = report.os_version
    contexts["os"] = os_context

    # Flat dump of the whole report so nothing is lost, even the fields that are not tagged.
    contexts["error_report"] = {
        "client_event_id": str(report.client_event_id),
        "occurred_at": report.occurred_at.isoformat(),
        "received_at": received_at.isoformat(),
        "source": source,
        "platform": report.platform,
        "app_version": report.app_version,
        "app_build": report.app_build,
        "device_brand": report.device_brand,
        "device_model": report.device_model,
        "os_version": report.os_version,
        "screen": report.screen,
        "operation": report.operation,
        "had_cached_data": report.had_cached_data,
        "error_kind": report.error_kind,
        "error_type": report.error_type,
        "error_message": error_message,
        "stack_trace": stack_trace,
        "request_method": report.request_method,
        "request_path": report.request_path,
        "request_query": request_query,
        "response_status": report.response_status,
        "response_body": response_body,
        "backend_request_id": report.backend_request_id,
        "trace_truncated": report.trace_truncated,
        "response_truncated": report.response_truncated,
    }

    extra: dict[str, Any] = {}
    if stack_trace is not None:
        extra["stack_trace"] = stack_trace
    if response_body is not None:
        extra["response_body"] = response_body

    return {
        # Sentry dedupes on event_id, so a client retrying the same report is a no-op server-side.
        "event_id": report.client_event_id.hex,
        "timestamp": report.occurred_at,
        "platform": "other",
        "level": "error",
        "logger": "mobile.error_report",
        "message": f"[{report.platform}] {report.error_kind}: {report.error_type}: {error_message[:200]}",
        # The leading namespace guarantees these never group with the backend's own events.
        "fingerprint": [
            "mobile-error-report",
            report.platform,
            report.error_kind,
            report.error_type,
            report.request_path or report.screen or "",
        ],
        "tags": tags,
        "contexts": contexts,
        "extra": extra,
    }
