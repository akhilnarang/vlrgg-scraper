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
| GET | `/matches/live` | Stream live match state over SSE (on by default; needs Redis) |
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

## Live match streaming

`GET /matches/live?match_id=123&match_id=456` is a Server-Sent Events (SSE) stream of
compact score updates for the watched ids. It is **on by default**. It needs Redis but not
response caching: live coordination uses its own Redis keys, so `ENABLE_CACHE` is separate.
Without Redis, the endpoint returns `503` and the cron job does not run. Set
`ENABLE_LIVE_MATCHES=false` to disable the endpoint and its cron job.

The stream is built for iOS Live Activities and Android live updates. It carries team
names, team logos, the current BO3/BO5 series score, the current map name, and the
current map score. It does **not** carry team ids, raw status, maps history, event
metadata, players, rounds, bans, streams, VODs, or previous encounters. Use the normal
match endpoint when a client needs that data.

Each connection watches up to `LIVE_MAX_MATCHES` ids. An id is 1 to 10 ASCII digits.
Leading zeros are removed, so `00123` and `123` are the same id. An id that is not ASCII
digits or is longer gets `400`. The server registers an expiring Redis lease per id, polls
shared snapshots in one batched read every `LIVE_POLL_INTERVAL` seconds, and emits each new
observation once. Every successful cron refresh creates a new observation, so a connection
still receives a version when the scores did not change. Subscribers to the same id share a
single upstream fetch per scheduled occurrence, and no connection ever scrapes VLR directly.
Occurrences are not fenced: if one occurrence runs longer than 30 seconds, the next
occurrence can overlap it. There is no event replay: after a reconnect the client receives
the current shared state.

Each live update is `event: snapshot` with JSON `data`:

```json
{
  "match_id": "123",
  "version": 1767225600000,
  "observed_at": "2026-01-01T00:00:00Z",
  "terminal": false,
  "teams": [
    {"name": "Alpha", "img": "https://cdn.vlr.gg/a.png", "score": 1},
    {"name": "Beta", "img": "https://cdn.vlr.gg/b.png", "score": 0}
  ],
  "current_map": {"name": "Ascent", "scores": [8, 6]}
}
```

Each event id is `<match_id>:<version>`, for example `123:1767225600000`. `current_map`
is `null` until the match has a map. Team scores and current-map scores are `null` when
VLR has no value. A team logo is an absolute URL or `null`.

`teams` always follows the match-header order. `current_map.scores` also follows the
header order: the projection matches the map-card teams by stripped, case-folded name. If
a header team cannot be matched to exactly one map team, its score is `null`. The stream
selects one map only and never sends a maps array. It uses the latest map with a nonzero
score or rounds. When no map has started, it uses the first map, so a fresh map shows
`0-0`.

The final update uses `event: end` with `terminal: true`. Its `teams[*].score` holds the
final series score and `current_map.scores` holds the final map score. The projection treats
the parser status `final` and the legacy status `completed` as terminal. A client must
render the final data and stop its reconnect loop for that match. The server then removes
the match from reads and renewals and releases its lease, so no further event arrives for
it. Other matches in the same stream continue. The stream closes when every watched match
has ended. A Redis failure or a client disconnect closes the stream without an `end`
event; the client can reconnect to get the current state.

While no snapshot changes the server sends SSE keepalive comments, and it sets
`Cache-Control: no-cache` and `X-Accel-Buffering: no` for proxies.

### Client and deployment limitations

- Browser `EventSource` cannot set an `Authorization` header. When API keys are enabled,
  use a polyfill or a server-side/proxy connection that can send `Authorization: Bearer ...`.
- The response must not be buffered by a reverse proxy. Nginx respects
  `X-Accel-Buffering: no`, but the proxy's read timeout must exceed the keepalive interval.
- Redis is required. If live mode is disabled or Redis is unavailable,
  `GET /matches/live` fails closed with `503` before any scrape. A mid-stream Redis
  failure ends the stream (the lease TTL reaps the connection) so the client can reconnect.

### Permanent synthetic match

Match id `3141592653` is a built-in test feed whenever live streaming is enabled. It
uses the same SSE, Redis lease, cron, and reconnect paths as a real match, but every cron
tick generates `SSE Test Alpha vs SSE Test Beta` locally and never requests VLR. The
updates walk four phases:

1. Series `0-0`, current map Haven `4-2`.
2. Series `0-0`, current map Haven `9-7`.
3. Series `1-0`, current map Ascent `5-4`.
4. Series `2-0`, current and final map Ascent `13-10`, sent as `event: end`.

The server releases and resets the feed after the final event, so the next subscription
starts at the first phase.

```bash
curl --no-buffer \
  --header "Accept: text/event-stream" \
  --header "Authorization: Bearer $VLRGG_API_KEY" \
  "http://localhost:8000/api/v1/matches/live?match_id=3141592653"
```

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
