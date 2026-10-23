# Background Jobs (Cron)

The application uses arq for background job scheduling to periodically update cached data.

## Overview

- **Framework**: arq (Redis-based job queue)
- **Scheduling**: Cron-like syntax for job timing
- **Async**: Asynchronous job execution
- **Persistence**: Jobs survive restarts via Redis

## Job Schedule

| Job | Function | Schedule | Purpose |
|-----|----------|----------|---------|
| Rankings | `rankings_cron` | Every 30 min | Update team rankings |
| Matches | `matches_cron` | Every 5 min | Update match listings |
| Riot Matches | `riot_matches_cron` | Every 5 min at :02, :07, … | Archive every completed game Riot's esports match API lists, then link the unlinked ones to VLR maps (`app/cron/riot_matches.py`); runs when `RIOT_API_KEY` is set, and links only when `LLM_API_KEY` is also set |
| Events | `events_cron` | Every 30 min | Update event listings |
| News | `news_cron` | Every 30 min | Update news articles |
| Standings | `standings_cron` | Daily 00:00 | Update current year standings |
| Team Rankings | `team_rankings_cron` | Every 15 min | Upsert newly completed matches and rebuild the Elo ledger (`app/cron/team_rankings.py`) |
| FCM Notifications | `fcm_notification_cron` | Every 15 min | Legacy "match starting soon" alert on the `match-`/`event-`/`team-` topics (`app/cron/legacy_fcm.py`) |
| Live Matches | `live_push_cron` | Every minute | Send APNs/FCM scores for tracked matches (listed live, or already pushed); at most one run at a time (fixed arq `job_id`) |
| Riot Web Schedule | `riot_web_schedule_cron` | Every 10 min at :01, :11, … | Store Riot's web schedule and link each series to its VLR match (`app/cron/riot_web.py`); runs when `RIOT_WEB_API_KEY` is set |
| Riot Web Details | `riot_web_details_cron` | Every 10 min at :04, :14, … | Fetch `getEventDetails` for linked series that are due: at most 30 a run, refreshed hourly, settled 48 hours after the start |
| Riot Web Brackets | `riot_web_brackets_cron` | Every 10 min at :07, :17, … | Refresh the standings of Riot tournaments playing now |

The team rankings job upserts every completed match that is missing from the ledger,
stored without both teams, a decisive score, or every played map, or contradicted by
the listing's teams and scores. It then replays the whole ledger into the series Elo
(`k48-hnone-m0.3-r0`, no decay), the per-map Elo, and the circuit counts. The replay
is deterministic, so a fetch or write failure only leaves that match for the next
run and can never move a rating out of order. Each upserted match keeps VLR's event
patch, which `/api/v2/rankings/predict` forwards to the external prediction service
as the model's history scope. Historical database seeding and
catch-up are manual deployment steps; import scripts are not included in this
change. Complete them before enabling ongoing ingestion.

The live push cron runs at most once at a time. It fetches every match the listing
shows as live plus every match with stored push state. A tracked match is ended only
by VLR fetch failures while VLR no longer lists it as live: a 404, or a 5xx on three
consecutive runs (`valesports:push:fetch_failures:<match>`, 600-second TTL), using the
last score sent. Parser errors and unreachable-VLR errors are logged without
counting toward the streak. While the broadcast tracker reports a healthy read and
its latest score was delivered, the tracker drives that match's pushes and the cron
only refreshes its metadata; the cron resumes pushing when the tracker errors, goes
quiet, or a delivery fails. The tracker's score endpoint schedules the same push and
refresh work as in-process FastAPI background tasks, so those two paths are not arq
jobs and do not appear in the schedule above.

When the tracker is up but VLR cannot serve it — its score stalls over a match VLR keeps
pushing, or its team codes match no VLR match for 30 minutes — the cron logs an ERROR,
which Sentry turns into an event. See
[Broadcast tracker alerts](live-alerts.md) for both alert paths and the rule runbook.

`RIOT_WEB_API_KEY` is the only switch for the Riot web overlays: **set, every Riot web
job runs; unset, no Riot web call is made** and the matching responses are VLR-only.
There are no feature flags, and `ENABLE_CACHE` is not needed for them: the cache
wrappers no-op when caching is off, so the jobs still store what they fetch.

With the key set, the live push cron also reads Riot's `getLiveDetails` list to find
matches VLR's listing has not caught up to. The answer is cached for 60 seconds in
`valesports:riot_web:live`, matching Riot's own CDN window. Riot is additive only: it
decides which VLR pages the cron reads, and VLR's detail still decides every push, ending
and final. Riot's list lingers about 11 minutes after a series ends, so a Riot-only id
with no stored push state whose VLR status is already final is skipped instead of
resending the Android FINAL every minute. That linger can also defer the fetch-failure
path, so it does so only for a five-minute grace window starting at the first consecutive
failed read; after that VLR's failure decides. Riot never ends a match: its list omits
whole leagues, so only VLR can end one.

With the key unset, live discovery stops, `map_vods` is empty and brackets serve empty
`sections`; the rest of each response is unchanged.

The Riot web schedule job runs whenever `RIOT_WEB_API_KEY` is set. It walks
`getSchedule` by `pageToken` (never `page=`), at most five pages and stopping at the
three-day lookback edge, then upserts `riot_web_matches` and
links a series only when the normalised team-name pair and the start time agree within two
hours (six hours only when exactly one candidate fits). Two or more candidates are logged
and left unlinked. A link is written once and never rewritten, so a wrong link stays until
it is cleared by hand:

```sql
UPDATE riot_web_matches SET vlr_match_id = NULL, vlr_event_id = NULL
WHERE riot_id = '<riot id>';
```

The next run may link it again from scratch. If a run logs `Riot and VLR times disagree for
every name match`, at least three name pairs were found and none was inside two hours: the
host's `TIMEZONE` no longer matches the zone VLR's schedule renders in, so check that
before suspecting the alias table.

The details job runs whenever `RIOT_WEB_API_KEY` is set, every 10
minutes at :04, :14, …. It fetches `getEventDetails` for at most 30 linked series that are
due — never fetched, or fetched over an hour ago while the series started less than 48
hours ago — and stores each series' per-map VOD offsets and `tournament_id`. After the 48
hours a series is settled and never fetched again. `event: null` for most 2021–2023 ids is
expected and leaves `tournament_id` NULL. Every id is fetched before any write, so no
transaction spans network I/O; a rejected key or a `429` ends the run after storing what
was already fetched.

The Riot web brackets job runs whenever `RIOT_WEB_API_KEY` is set. It refreshes
`getStandings?tournamentId=…` for every tournament that has a linked series within two
days of now, one tournament per transaction, so no row lock spans a Riot call. A
rejected key or a `429` ends the run; other failures skip that tournament. An empty
standings answer is stored as `NULL`, and the route does not ask again for that
tournament for 24 hours. Old tournaments that no longer have a match in the window are
not refreshed; their last stored layout keeps serving.

**Rotating `RIOT_WEB_API_KEY`.** Riot rotates the site's frontend key without notice. A
Sentry ERROR `Riot web rejected RIOT_WEB_API_KEY` (throttled to one an hour) means it was
rejected. Open https://valorantesports.com with devtools → Network, filter `esports-api`,
copy the `x-api-key` request header, check it with

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H "x-api-key: $KEY" \
  'https://esports-api.service.valorantesports.com/persisted/val/getLiveDetails?hl=en-US&sport=val'
```

(a valid key prints `200`; a rejected one prints `403`), then set `RIOT_WEB_API_KEY` in
`~/vlrgg-scraper/.env` and restart the service. Removing `RIOT_WEB_API_KEY` disables
the jobs and leaves every Riot-web field empty, and VLR responses are unaffected.

## Riot esports match archive

`riot_matches_cron` is a separate integration on Riot's official esports match API and is
gated only on `RIOT_API_KEY`, not on `RIOT_WEB_API_KEY`. It archives every completed game
Riot lists, whether or not VLR has a map for it yet. Linking an archived game to its VLR
map asks the model, so it runs only when `LLM_API_KEY` is also set; without it the archive
still runs and only the linking step is skipped. See the job table above for its schedule.

The run has a four-minute budget and stops early when Riot answers `429`; whatever was
found so far is still stored. A game already archived and linked is skipped, and a game
the model turned down, or one that competes for a map another game also claims, is
remembered in Redis for 13 hours and not asked about again.

## Implementation

### Job Functions (`app/cron/jobs.py`, `app/cron/legacy_fcm.py`, `app/cron/live_push.py`, and `app/cron/team_rankings.py`)

Each job function:
1. Takes a `ctx` dict (Redis connection, etc.)
2. Performs scraping/parsing
3. Updates Redis cache with fresh data
4. Logs execution

Example:
```python
async def rankings_cron(ctx: dict) -> None:
    get_current_scope().set_transaction_name("Rankings Cron")
    result = await rankings.ranking_list()
    await ctx["redis"].set("rankings", json.dumps([...]), ex=3600)
```

### Worker Setup

`app/cron/worker.py` schedules the jobs:

```python
cron_jobs = [
    cron("app.cron.jobs.rankings_cron", hour=None, minute={0, 30}),
    # ... other jobs
]

worker = create_worker({"cron_jobs": cron_jobs})
```

## Configuration

Jobs are configured in `ArqWorker.start()` method. Requires Redis connection.

## Monitoring

- **Logs**: Each job logs start/completion
- **Redis**: Job status visible in Redis
- **Metrics**: Job execution times and success rates

## Error Handling

- **Retries**: arq handles job retries on failure
- **Logging**: Failed jobs logged with stack traces
- **Isolation**: Job failures don't affect main app

## Development

To run jobs locally:
```bash
# Jobs run automatically with the FastAPI server
uv run fastapi dev
```

To test jobs manually:
```python
from app.cron.jobs import rankings_cron
await rankings_cron({"redis": redis_client})
```
