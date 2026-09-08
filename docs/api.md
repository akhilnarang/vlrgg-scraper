# API Documentation

This document provides general information about the API. For detailed endpoint specifications, see the interactive API documentation at `/docs` (Swagger UI) or `/redoc` (ReDoc).

## Base URL
```
http://localhost:8000/api/v1/
```

## Authentication
Most endpoints are public. Some internal endpoints may require `X-API-Key` header.

## Available Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/events` | List events with optional status filtering |
| GET | `/events/{id}` | Get detailed event information |
| GET | `/matches` | List matches with filtering options |
| GET | `/matches/{id}` | Get detailed match information |
| GET | `/news` | Get latest news articles |
| GET | `/news/{id}` | Get an article with ordered content blocks |
| GET | `/player/{id}` | Get player statistics |
| GET | `/rankings` | Get current team rankings |
| GET | `/standings/{year}` | Get VCT standings for a year |
| GET | `/team/{id}` | Get team information |
| GET | `/search` | Search teams, players, and events |
| GET | `/version` | Get API version info |

## News article content

`GET /news/{id}` returns `blocks` in article document order. Clients should render
these blocks directly; no client-side HTML parsing is required. Text, links, media,
and nested content remain in their original positions.

| Block `type` | Content |
| --- | --- |
| `paragraph` | `runs` of text |
| `heading` | `runs` and heading `level`, from 1 to 6 |
| `blockquote` | Ordered `children` blocks |
| `list` | `children` containing `list_item` blocks; `ordered` and `start` specify numbering |
| `list_item` | Ordered `children`, including paragraphs and nested lists |
| `image` | Absolute `url` and `alt` text; optional `link_url` when wrapped in a link |
| `video` | Absolute `url`, including iframe embeds and native video sources; optional `link_url` when wrapped in a link |
| `caption` | `runs` from a figure caption or italic media caption |

Each text run has `text`, an optional absolute link `url`, and `bold` and `italic`
flags. Concatenate runs exactly, retaining their spaces and newlines. A line break
is represented by `\n` within a run. Formatting and links can overlap. Link and
media URLs are resolved against VLR and emitted only when they use HTTP or HTTPS.
When an image or video is wrapped in an anchor link, its destination is preserved in
`link_url`.

For example, an introduction followed by a video and an interview question has
this block sequence. Unused fields are omitted here for readability; responses
include their defaults.

```json
[
  {"type": "paragraph", "runs": [{"text": "Interview introduction.", "italic": true}]},
  {"type": "video", "url": "https://www.youtube.com/embed/example"},
  {"type": "paragraph", "runs": [{"text": "How did the match feel?", "bold": true}]},
  {"type": "blockquote", "children": [
    {"type": "paragraph", "runs": [{"text": "We felt prepared."}]}
  ]}
]
```

The existing `content`, `links`, `images`, and `videos` fields are generated from
the same blocks. `content` uses `{{link_N}}`, `{image_N}`, and `{video_N}` as
zero-based references to the corresponding arrays. These references now occur
at their document positions, and heading text is included. Use `blocks` to retain
heading levels, emphasis, quotes, and list structure.

Article detail responses are scraped on request, unlike the cached news list.
Clients with locally cached article bodies should fetch an article again when
its cached response has no blocks.

## Mobile error reports

`POST /error-reports/` ingests client-side error reports from the mobile app (issue
#598). This is the Sentry-backed variant of that endpoint: reports are forwarded to
Sentry as events instead of being written to a database, so the route is mounted only
when `SENTRY_DSN` is configured and is absent (404) otherwise.

### Authentication

This endpoint always requires bearer authentication, even when the rest of the API is
served openly: send `Authorization: Bearer <key>` with a key configured in `API_KEYS`.
The *source* name of the matching entry is recorded on the resulting Sentry event; the
token itself is never stored or forwarded. With `API_KEYS` empty, every request is
rejected with 401.

### Request body

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `client_event_id` | UUID | yes | Client-generated; doubles as the idempotency key |
| `occurred_at` | datetime | yes | Must be timezone-aware; naive timestamps are rejected |
| `platform` | `android` \| `ios` | yes | |
| `app_version` | string | yes | Max 64 characters |
| `app_build` | string | yes | Max 64 characters |
| `device_brand` | string | no | Max 64 characters |
| `device_model` | string | no | Max 128 characters |
| `os_version` | string | no | Max 64 characters |
| `screen` | string | no | Screen the failure surfaced on; max 128 characters |
| `operation` | string | no | Logical operation being performed; max 128 characters |
| `had_cached_data` | boolean | no | Whether the app could fall back to cached data |
| `error_kind` | `http` \| `network` \| `timeout` \| `parsing` \| `database` | yes | Cancellations are deliberately not accepted - an aborted request is not a failure |
| `error_type` | string | yes | Client exception/type name; max 256 characters |
| `error_message` | string | yes | Max 4096 characters; redacted server-side |
| `stack_trace` | string | no | Size-capped, see below; redacted server-side |
| `request_method` | string | no | Max 16 characters |
| `request_path` | string | no | Max 512 characters |
| `request_query` | object of string | no | Filtered to an allowlist, see below |
| `response_status` | integer | no | 100-599, as observed by the client |
| `response_body` | string | no | Size-capped, see below; redacted server-side |
| `backend_request_id` | string | no | Correlates with the backend's own logs/events; max 128 characters |
| `trace_truncated` | boolean | no | Defaults to `false`; forced to `true` if the server truncates |
| `response_truncated` | boolean | no | Defaults to `false`; forced to `true` if the server truncates |

### Size limits

| Subject | Cap | Behaviour when exceeded |
| --- | --- | --- |
| Request body | 256 KiB | Rejected with 413 before the body is parsed |
| `stack_trace` | 64 KiB | Truncated on a UTF-8 codepoint boundary, `trace_truncated` set to `true` |
| `response_body` | 16 KiB | Truncated on a UTF-8 codepoint boundary, `response_truncated` set to `true` |

Clients should send the truncation flags they know about, but the server is
authoritative: a report that claims an untruncated payload while exceeding a cap is
stored with the flag forced on.

### Responses

`201 Created` is returned once the event has been captured and flushed to Sentry:

```json
{
  "id": "6f1a4c2e-2a4e-4a1f-9a55-1f0d1c1f2b3a",
  "received_at": "2025-01-31T12:34:56.789012+00:00"
}
```

`id` echoes `client_event_id`, and `received_at` is the server time of acceptance.

| Status | Meaning |
| --- | --- |
| `201` | Report captured and flushed |
| `401` | Missing or invalid bearer token |
| `413` | Request body larger than 256 KiB |
| `422` | Validation error (unknown `platform`/`error_kind`, naive `occurred_at`, over-long field) |
| `429` | Rate limited, per authenticated source, when `ERROR_REPORT_RATE_LIMIT_ENABLED` is on |
| `503` | Sentry capture is unavailable; the report was not recorded |

A 503 is scoped to this endpoint - the rest of the API keeps serving normally when
Sentry is unreachable.

Retries are idempotent through `client_event_id`: Sentry deduplicates by event id, so
resubmitting the same report is a no-op server-side and returns the same 201 with the
same `id`.

### Redaction

Free-form client text (`error_message`, `stack_trace`, `response_body`) is scrubbed for
credential material - bearer tokens and `api_key`/`token`/`authorization`/`password`/
`secret` assignments are replaced with `[REDACTED]` before anything reaches Sentry.
`request_query` is filtered to an allowlist (`page`, `tab`, `region`, `year`,
`category`, `type`, `timespan`, `group`, `series_id`, `tier`, `q`); any other parameter
is dropped entirely rather than stored as a placeholder.

### How reports appear in Sentry

Reports are ordinary Sentry events, tagged so a client report is always
distinguishable from an error the backend itself raised:

| Tag | Value |
| --- | --- |
| `error_origin` | Always `mobile-app`; the backend's own events never carry this tag |
| `report.source` | The authenticated API key source that submitted the report |
| `mobile.platform`, `mobile.app_version`, `mobile.app_build` | Reporting app build |
| `mobile.error_kind` | The reported `error_kind` |
| `mobile.screen`, `mobile.operation` | Set when the client provided them |
| `api.method`, `api.path`, `api.status` | What the *client observed*, when provided - `api.status:500` means the app saw a backend 500, not that the backend raised one |
| `backend_request_id` | Set when provided, to correlate with the backend's own event and logs |

The full report is also attached as an `error_report` context (redacted and truncated
values, plus `received_at` and the source), with the stack trace and response body
repeated under `extra`, and device/OS details in the standard `device` and `os`
contexts.

Grouping is pinned to a fingerprint namespaced under `mobile-error-report`
(`["mobile-error-report", platform, error_kind, error_type, request_path or screen]`),
so a client-observed backend 500 never merges into the backend's own 500 events.

### Differences from issue #598

- No PostgreSQL table, migration, or 30-day retention job; Sentry's own retention
  applies to captured reports.
- No `409` on a retry whose payload conflicts with a stored one - there is nothing to
  read back, and Sentry deduplicates by event id.
- The `201` guarantee is "captured and flushed to Sentry", not "persisted to a
  database".

## Interactive Documentation

- **Swagger UI**: Visit `http://localhost:8000/docs` for interactive API testing
- **ReDoc**: Visit `http://localhost:8000/redoc` for alternative documentation view
- **OpenAPI JSON**: `http://localhost:8000/openapi.json` for programmatic access

## Error Responses

All endpoints return standard HTTP status codes:

- `200`: Success
- `400`: Bad Request
- `404`: Not Found
- `422`: Validation Error (Pydantic validation errors)
- `500`: Internal Server Error

Error response format:
```json
{
  "detail": "Error message"
}
```

## Rate Limiting
No explicit rate limiting implemented. Please respect vlr.gg's servers and avoid excessive requests.

## Caching
Endpoints use Redis caching with TTL. Cache keys are set by background cron jobs. See [Caching Documentation](caching.md) for details.
