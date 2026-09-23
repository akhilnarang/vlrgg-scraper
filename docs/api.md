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

## Live match updates

Live updates are disabled by default. When enabled, clients can store one APNs
push-to-start token and replace their favorites:

```http
PUT /api/v1/live-updates/clients/{client_id}/token
Authorization: Bearer <api-key>

{"token":"<token>","platform":"iOS"}
```

`platform` is `iOS` (default) or `android`. iOS sends its hex APNs push-to-start
token; Android sends its FCM registration token. Only iOS tokens are used today;
Android tokens are stored for later, and Android live scores still come from topics.

```http
PUT /api/v1/live-updates/clients/{client_id}/favorites
Authorization: Bearer <api-key>

{"teams":["1"],"matches":["123"],"players":[],"events":["99"]}
```

To immediately start a Live Activity for an in-progress match without waiting for the next cron run:

```http
POST /api/v1/live-updates/clients/{client_id}/matches/{match_id}/live-activity
Authorization: Bearer <api-key>
```

Returns `204 No Content` on success, `404` if the client has no registered iOS token, and `400` if the match is not live.

Invalid tokens (including a non-hex iOS token) or favorite IDs return `422 Unprocessable Entity`.

The one-minute job checks matches whose listing status is `live`. Android live scores
are data-only FCM messages on the `live-match-{id}`, `live-event-{id}`, `live-team-{id}`,
and `live-player-{id}` topics, which updated Android clients must subscribe to. The legacy
`match-`, `event-`, and `team-` topics carry only the "match starting soon" alert, because
released app versions show every message on those as a notification; those installs keep
getting just that alert. A matching iOS favorite creates one APNs broadcast
channel and one push-to-start request per client. Final state ends and deletes the
channel. If VLR returns 404 for a tracked match, or its page fails to load on three
runs in a row (DNS failure, refused connection, timeout, or 5xx), it ends with the
last score sent to iOS (the final Android message goes to `live-match-{id}` only).
Provider errors are logged and skipped; there is no delivery history or retry state
machine.

Favorite match `3141592653` on a test device, then `POST /api/v1/live-updates/test-match`
with your API key to start a synthetic match. It updates each minute and ends on tick 6;
trigger it again to restart. Triggering while it is running returns `409 Conflict`.

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
- `503`: VLR.gg can't be reached (DNS failure, refused connection, timeout)

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
