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

## Team rankings (v2)

`/api/v2/rankings` ranks teams by Elo computed from the match ledger in the
application database, not from VLR's points table. The series rating follows the
`k48-hnone-m0.3-r0` algorithm: every result on a calendar day is scored against
the ratings as they were before that day, so a team that plays twice on a day
meets both opponents with the same rating; a 48-point K-factor moves the winner
and loser equally; and a complete map score blends into the result with weight
0.3 (`observed = 0.7 * series_outcome + 0.3 * map_share`). Ratings never decay,
so an inactive team keeps its rating; played maps keep a separate Elo for map
predictions and head-to-head map records. The list ranks by series Elo unless a
different sort is requested.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v2/rankings/` | Ranked team list |
| GET | `/api/v2/rankings/teams/{id}` | Team Elo profile, form, and recent results |
| GET | `/api/v2/rankings/predict?team_a={id}&team_b={id}` | Match and map win probabilities with their sources and warnings, plus head-to-head history |

`/api/v2/rankings/predict` calculates match and map win probabilities and
head-to-head history. When `PREDICTION_SERVICE_URL` is configured (HTTP URL or
Unix socket `unix:///path/to/socket.sock`), the series win probability is queried
from the external ML prediction service with automatic fallback to Elo when
unconfigured or unusable; the response says which.

Both `match` and `map` carry complementary `team_a`/`team_b` probabilities, the
`source` that produced them, and typed `warnings`:

- `{"kind": "model", "model_version": ..., "history": {...}, "coverage": {...}}`
  is the `match` source when the service answered. `history` is the model's
  latest observation (`max_date`, `freshness_days`); `coverage` is the evidence
  behind the prediction (`history_matches`, `history_max_played_on`,
  `head_to_head_results`, `patch_scope`, and per-team `match_results`,
  `map_results`, `map_name_results`, and `patch_map_results`, each with its
  effective time-weighted count and `last_seen`).
- `{"kind": "elo", "rating": "series"}` is the `match` source when stored Elo
  produced the probability. `map` always uses
  `{"kind": "elo", "rating": "map"}`, because the service predicts one named map
  at a time.
- `warnings` lists typed indicators. Model warnings appear as a bare code
  (`unknown_team`, `low_coverage`, `unknown_patch`, `no_eligible_history`,
  `no_team_map_history`, `limited_team_map_history`, `map_not_in_model`,
  `unvalidated_map_fallback`) for clients to map to their own wording. An Elo
  fallback adds `{"code": "elo_fallback", "reason": ...}`, where `reason` is one
  of `model_not_configured`, `model_timeout`, `model_http_error`,
  `model_unavailable`, or `model_invalid_response`. A code the scraper does not
  recognize stays visible as `{"code": "unknown", "upstream_code": ...}`, with
  the upstream `message` when one was supplied.

The list accepts `circuit` (`vct`, `vcl`, `t3`, `gc`, `collegiate`,
`offseason`, `other`), `min_matches` (default 5), `include_inactive`, `sort`
(`elo`, `map_elo`, `matches`, `win_rate`), `order` (`asc`, `desc`), `limit`,
and `offset`. A team appears after at least `min_matches` rated series inside the
last 180 days and, unless `include_inactive` is set, a match within the last 90
days. Each item carries its rank in the requested selection, its overall rank
(ignoring the circuit filter), the stored ratings, win-loss records, circuits, and
the primary circuit it played most. A circuit is derived from the event's VLR tier
listing and refreshed from the tier pages when a new event is first ingested.

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

## Match map entries

`GET /matches/{id}` returns `data` with one entry per game whose stats panel VLR has
rendered, in series order. Each entry's `number` is its true 1-based game number from
VLR's map navigation, so a game VLR lists without a panel, or names TBD or N/A, is
omitted without shifting a later game's number: entry numbers can skip. No entry has an
empty `map` or empty `teams`. `map_count` counts the games VLR shows as played or in
progress and can differ from the number of entries in `data`.

## Live match updates

Live updates are disabled by default. When enabled, clients can store one APNs
push-to-start or FCM registration token and replace their favorites:

```http
PUT /api/v1/live-updates/clients/{client_id}/token
Authorization: Bearer <api-key>

{"token":"<token>","platform":"iOS"}
```

`platform` is `iOS` (default) or `android`. iOS sends its hex APNs push-to-start
token; Android sends its FCM registration token. Android tokens receive an immediate
unicast FCM message on `POST .../live-activity`; recurring score updates are sent
directly to the same registration token, never through a topic.

```http
PUT /api/v1/live-updates/clients/{client_id}/favorites
Authorization: Bearer <api-key>

{"teams":["1"],"matches":["123"],"players":[],"events":["99"]}
```

To immediately start live updates for an in-progress match without waiting for the next cron run:

```http
POST /api/v1/live-updates/clients/{client_id}/matches/{match_id}/live-activity
Authorization: Bearer <api-key>
```

Returns `204 No Content` on success (triggers an APNs push-to-start on iOS, or an immediate direct FCM message to the device on Android), `404` if the client has no registered token, and `400` if the match is not live.

Invalid tokens (including a non-hex iOS token) or favorite IDs return `422 Unprocessable Entity`.

The one-minute job checks matches whose listing status is `live`. Android live scores
are data-only, high-priority FCM messages sent directly to each follower's stored
registration token; there is no topic fanout and no topic fallback. The legacy
`match-`, `event-`, and `team-` topics carry only the "match starting soon" alert, because
released app versions show every message on those as a notification; those installs keep
getting just that alert. A matching iOS favorite creates one APNs broadcast
channel and one push-to-start request per client. A final state ends the Live
Activity, which stays on the Lock Screen with the final score for four hours (the
most ActivityKit allows) before the system dismisses it, and deletes the channel. A
tracked match is ended only by VLR fetch failures while VLR no longer lists it as
live: a 404, or a 5xx on three consecutive runs. Parser errors never end a match,
and a stored state with no play is dropped without a broadcast instead of
announced as "FINAL 0-0". The end carries the last score sent to iOS and to each
Android follower's token.
Provider errors are logged and skipped; there is no delivery history or retry state
machine.

The compact state carries `observed_at` (absolute server epoch seconds) and `stage`, VLR's
stage label verbatim (e.g. `Playoffs: Grand Final`) or `null` when VLR renders none. It may
carry a `pause` with its `kind` and an optional `reason`. A client derives "paused since"
from `observed_at`: the inbound tracker field `pause.since` is that tracker's own relative
second and is never forwarded.

The state also carries `map_round_winners`: one entry per map slot, in `map_winners` order,
each naming the slot's 1-based `map_number` and its `winners`, holding each played round's
winner as an index into `teams` (`0` or `1`), or `null` when a round has no winner. A map
slot with no rounds has an empty `winners` list.

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
