# Background Jobs (Cron)

The application uses arq for background job scheduling to update cached data and to
refresh live match snapshots.

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
| Events | `events_cron` | Every 30 min | Update event listings |
| News | `news_cron` | Every 30 min | Update news articles |
| Standings | `standings_cron` | Daily 00:00 | Update current year standings |
| FCM Notifications | `fcm_notification_cron` | Every 15 min | Send match notifications |
| Live Matches | `live_matches_cron` | Every 30s (seconds 0, 30) | Refresh shared snapshots for watched matches |

arq starts when `ENABLE_CACHE` or `ENABLE_LIVE_MATCHES` is on. The response-cache jobs
(rankings, matches, events, news, standings) and the FCM notification job register only
with `ENABLE_CACHE`. `live_matches_cron` registers only with `ENABLE_LIVE_MATCHES`, so a
live-only worker runs just the live job.

`live_matches_cron` prunes expired
leases, then fetches every actively watched distinct match exactly once per tick with
bounded concurrency and stores one shared full snapshot per match. Zero active watchers
means zero upstream detail requests, and a failed fetch leaves the previous snapshot in
place.

arq schedules one occurrence every 30 seconds. Each occurrence fetches every actively
watched distinct match once, with concurrency bounded by `LIVE_FETCH_CONCURRENCY`.
Occurrences are not fenced: if one occurrence runs longer than 30 seconds, the next
occurrence can overlap it.

## Implementation

### Job Functions (`app/cron.py`)

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

```python
cron_jobs = [
    cron("app.cron.rankings_cron", hour=None, minute={0, 30}),
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
from app.cron import rankings_cron
await rankings_cron({"redis": redis_client})
```

