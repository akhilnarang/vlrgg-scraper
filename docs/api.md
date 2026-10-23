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
| GET | `/events/{id}/brackets` | Get Riot's bracket layout for an event |
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

## Event brackets

`GET /events/{id}/brackets` returns Riot's own bracket layout for a VLR event. It is
additive: `/events/{id}` is unchanged, and `sections` empty is still valid. Riot data
is enabled by `RIOT_WEB_API_KEY`; with the key unset or the event not linked, the
response is `200` with `"sections": []` and a `status` that says why. There are no
feature flags: setting the key enables every Riot web overlay, and removing it disables
them all. A mapping gap is not a client error, so the route never answers `404` for it
and never fails because of Riot.

```json
{
  "event_id": "2863",
  "available": true,
  "status": "ok",
  "fetched_at": "2026-03-16T15:04:00Z",
  "sections": [
    {
      "id": "112019385324163480",
      "tournament": "Masters Madrid",
      "stage": "Playoffs",
      "name": "Playoffs",
      "columns": [
        {
          "cells": [
            {
              "name": "Round 1",
              "matches": [
                {
                  "id": "112019385324229017",
                  "vlr_match_id": "1234",
                  "label": "M11",
                  "status": "completed",
                  "start_time": "2024-03-16T15:00:00Z",
                  "best_of": 3,
                  "slots": [
                    {
                      "team": {"id": "105795137629057449", "name": "Paper Rex", "code": "PRX", "img": "https://static.lolesports.com/teams/paper-rex.png", "vlr_team_id": "624"},
                      "source": {"kind": "seed", "match_id": null, "result": null, "seed": 2},
                      "score": 0,
                      "winner": false
                    },
                    {
                      "team": {"id": "105665378258561492", "name": "Gen.G", "code": "GEN", "img": "https://static.lolesports.com/teams/gen-g.png", "vlr_team_id": null},
                      "source": {"kind": "seed", "match_id": null, "result": null, "seed": 1},
                      "score": 2,
                      "winner": true
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

The response fields:

- `sections` is unchanged: the bracket to render, or an empty list.
- `available` is `true` exactly when `sections` is not empty.
- `status` says what the response covers:
  - `ok` — every tournament mapped to the event answered, and at least one section belongs
    to it.
  - `no_bracket` — Riot answered and the event owns no bracket: the tournament has no
    layout, or none of its sections is proven to be this event's. Safe to show "no bracket".
  - `unavailable` — the answer is unknown: the event is not linked to a Riot tournament,
    `RIOT_WEB_API_KEY` is unset, the database could not be read, or Riot could not be
    reached. Try again later rather than showing "no bracket".
  - `stale` — sections are returned, but a tournament mapped to the event could not be
    read, so the bracket may be incomplete.
- `fetched_at` is the fetch time of the oldest layout the returned sections came from, or
  `null` when there are no sections. The app can show it as "last updated"; a tournament
  that ended long ago keeps its final layout, so an old value is not by itself a problem.

Layout and fields:

- `columns` are Riot's left-to-right layout layers, not round numbers. A column can
  hold an upper-bracket and a lower-bracket cell. `cells` are display groups and can
  contain more than one match; two `Finals` matches (a final and a third-place
  decider) are normal. Column, cell and match order is exactly Riot's; nothing is
  sorted.
- `matches[].slots` always has two entries in Riot's display order. `team` is `null`
  for a TBD slot; the slot's `source` still says where its participant comes from.
- `source.kind` is `match` when the participant is the winner or loser of an earlier
  match, with `match_id` (the Riot series id) and `result` (`winner`/`loser`).
  Otherwise it is `seed`, with Riot's 1-based `seed` index; that covers qualification
  pools and byes, which Riot represents as a seed rather than a bye object.
- `matches[].status` uses the app's vocabulary: `upcoming`, `live`, `completed`, or `tbd`.
  `score` is the series map score and is `null` before a match starts or for a TBD
  slot; `winner` is `null` until the match is completed or live.
- `vlr_match_id` makes a bracket match tappable via `/matches/{id}` when the series
  was linked; it is `null` for a series the schedule sync has not linked, so use
  `matches[].id` for identity. `start_time` and `best_of` come from the stored schedule
  row, so an older Riot series whose details Riot no longer serves can still render
  without them.
- `team.id` is Riot's team id, not VLR's. `team.vlr_team_id` is the VLR team id, so the
  app can open `/team/{id}`, and is `null` when the Riot team is not matched to a VLR
  team. Only the mirrored Riot teams (`teams.riot_name`/`riot_code`, written by
  `scripts.mirror_team_logos`) are matched, and only when Riot's name and code agree on
  one VLR team; anything else stays `null` rather than being guessed.
- `label` is Riot's free-form description (`M11`, `UB Final`, `3rd Place Decider`).
  Descriptions and cell names are not unique identifiers; use `matches[].id`.
- Only `type: "bracket"` sections are returned. Riot's `group` and `crossGroup`
  sections are tables VLR standings already cover.
- `tournament`, `stage` and `name` are Riot's display names; a section can contain
  multiple independent brackets (for example four groups), and the response does not
  split or relabel them.

The VLR event is mapped to a Riot tournament through the ledger: each Riot series is
linked to one VLR match (`riot_web_matches.vlr_match_id`, written once by the
schedule cron), and `getEventDetails` supplies that series' `tournament.id`. An event
therefore sees every tournament any of its linked matches belongs to. Riot's Game
Changers LATAM, for example, spans several VLR events (North and South), and one VLR
event can hold several Riot tournaments.

A section is served only when a link proves it belongs to the requested event: its
linked matches must all name that event. A section with no linked match is served only
while the whole tournament is known to map to this one VLR event, because a tournament
shared by several events would otherwise show one event another's bracket. A section
whose links name another event, or several, is withheld whole, including its unlinked
matches.

The route serves stored data. When a tournament has no stored layout yet, it fetches
`getStandings` once (5-second timeout), stores the layout in PostgreSQL and
converts it on read. Converted responses are cached in Redis for 60 seconds, matching
Riot's CDN window; a cached payload that no longer parses is treated as a miss.
Riot's `getStandings` answers `502` without a tournament id, and an unreadable or
unknown answer is skipped rather than failing the route.

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

## Match map VODs

`GET /matches/{id}` also returns `map_vods`: Riot's VOD offsets aligned with the maps
VLR shows, as `[{"map_number": 1, "links": [{"provider": "youtube", "locale": "en-US",
"start_seconds": 360, "url": "..."}]}]`. An entry appears only for a completed map that
has at least one usable link. Links are best first: English locales before others, then
YouTube before Twitch. `start_seconds` is Riot's `startMillis` floored to seconds, and
`url` opens the provider's player at that offset; no other Riot timing field is used.

Riot numbers its games but gives no map name or per-map score, so its offsets are served
only when its completed game numbers are exactly VLR's completed map numbers **and** the
series scores agree. Otherwise `map_vods` is `[]`, because a mismatch could attach a VOD
to the wrong map. VLR's own `videos.vods` are unchanged and remain in the same response.

`map_vods` is always present as an additive key, even when `RIOT_WEB_API_KEY` is unset,
in which case it is `[]`. Making it unconditional is deliberate: clients get a stable
shape instead of a key that appears and disappears with the key's presence.

## Match insights

`GET /matches/{id}` also returns `insights` (or `null`): a model `prediction` of the
series, shaped like the v2 prediction's `match` plus `team_a_id`, `team_b_id`, and
`generated_at`, and plain-text `pre_match_summary` and `post_match_summary`, each with
`text` and `generated_at`. Each is generated once and then never changes:

- The prediction (when `PREDICTION_SERVICE_URL` is set) is generated on the first
  request within 24 hours before an upcoming match's start. Without the match's patch,
  it uses the latest stored patch, as `/api/v2/rankings/predict` does.
- The summaries are written by an LLM only when `ENABLE_MATCH_SUMMARIES` is `true`
  (default `false`) and `LLM_API_KEY` is set. The pre-match summary is generated in the
  same window from the two teams' head-to-head series over the last 180 days in the
  ranking ledger and the prediction; it is skipped when there is neither a head-to-head
  record nor a prediction.
- The post-match summary is generated on the first request after the final, once VLR
  shows every map of the series with its rounds and players, for a match that started
  within the last 7 days.

Generation needs Redis, which lets one request per match generate while concurrent ones
serve what is already stored, and is connected whenever the prediction or the summaries
are enabled, even with the cache off. The request
spends at most `INSIGHTS_REQUEST_BUDGET_SECONDS` (default 12), counted from its start,
on insights, including reading the stored ones: once the budget is spent, the details
are returned with the insights read so far, or `insights` set to `null` if none were read. A slower generation keeps running and its
result appears on a later request. A failed attempt is retried after five minutes, and
a summary skipped for lack of useful data after an hour. An insight made for
teams the match no longer shows is left out.

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
each naming the slot's 1-based `map_number`, its `winners`, holding each played round's
winner as an index into `teams` (`0` or `1`), or `null` when a round has no winner, and its
`scores`, the map's per-team scores in `teams` order (`null` while a team's score is
unknown, empty when the slot has no data). Positions are preserved, so a gap stays `null`
instead of collapsing the list; a finished map's trailing unplayed rounds are dropped.
Rounds come from VLR's rendered timeline as soon as it exists; a round VLR has not rendered
yet is inferred from consecutive broadcast-tracker scores when exactly one team gained a
round. A later VLR-rendered winner overrides an inferred one, and a corrected or lower
verified score clears the rounds it disproves. A map slot with no rounds has an empty
`winners` list. A team's series score is floored by its confirmed map wins, so a map win
that VLR's top header has not caught up to is still reflected immediately.

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
