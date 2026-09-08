from datetime import UTC, datetime

import sentry_sdk
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from starlette.concurrency import run_in_threadpool

from app.agent.ratelimit import enforce_rate_limit
from app.api.deps import get_api_key_source, get_redis_client
from app.constants import ERROR_REPORT_MAX_BODY_BYTES
from app.core.config import settings
from app.exceptions import PayloadTooLargeError, ServiceUnavailableError
from app.schemas import ErrorReportIn, ErrorReportResponse
from app.services.error_reports import build_sentry_event

router = APIRouter()


def enforce_body_size_limit(request: Request) -> None:
    """Reject oversized reports on the Content-Length header, before the body is validated.

    Declared as a route dependency so it runs ahead of body validation - the alternative
    (checking inside the handler) only fires after FastAPI has already parsed the payload.
    Trusting the header assumes a reverse proxy that enforces it, same as the /ask rate limit.
    """
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        declared_bytes = int(content_length)
    except ValueError:
        return
    if declared_bytes > ERROR_REPORT_MAX_BODY_BYTES:
        raise PayloadTooLargeError(detail=f"Report exceeds {ERROR_REPORT_MAX_BODY_BYTES} bytes")


@router.post("/", status_code=201, dependencies=[Depends(enforce_body_size_limit)])
async def submit_error_report(
    report: ErrorReportIn,
    source: str = Depends(get_api_key_source),
    redis_client: Redis = Depends(get_redis_client),
) -> ErrorReportResponse:
    """Ingest a client-side error report from the mobile app and forward it to Sentry.

    Rate limiting is keyed on the authenticated API key source rather than the client IP,
    since every report from an app install arrives through the same key. The event is
    captured on a cleared isolation scope so none of this ingestion request's own context
    (headers, transaction, the api_key tag) leaks into the client's event; what the SDK's
    request integration still attaches is stripped in `app.utils.before_send`. The response
    echoes the client's event id - retries of the same report are deduped by Sentry.
    """
    await enforce_rate_limit(
        redis_client,
        source,
        enabled=settings.ERROR_REPORT_RATE_LIMIT_ENABLED,
        limit=settings.ERROR_REPORT_RATE_LIMIT,
        window=settings.ERROR_REPORT_RATE_LIMIT_WINDOW,
        prefix="error-report",
    )

    received_at = datetime.now(UTC)
    event = build_sentry_event(report, source, received_at)

    client = sentry_sdk.get_client()
    if not client.is_active():
        raise ServiceUnavailableError(detail="Error reporting is unavailable")
    with sentry_sdk.isolation_scope() as scope:
        scope.clear()
        event_id = client.capture_event(event, scope=scope)
    if event_id is None:
        raise ServiceUnavailableError(detail="Error reporting is unavailable")
    # flush() blocks while the transport drains; keep it off the event loop.
    await run_in_threadpool(sentry_sdk.flush, 5)

    return ErrorReportResponse(id=report.client_event_id, received_at=received_at)
