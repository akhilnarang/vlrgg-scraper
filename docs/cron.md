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
| Events | `events_cron` | Every 30 min | Update event listings |
| News | `news_cron` | Every 30 min | Update news articles |
| Standings | `standings_cron` | Daily 00:00 | Update current year standings |
| FCM Notifications | `fcm_notification_cron` | Every 15 min | Legacy "match starting soon" alert on the `match-`/`event-`/`team-` topics (`app/cron/legacy_fcm.py`) |
| Live Matches | `live_push_cron` | Every minute | Send APNs/FCM scores for matches listed as live; at most one run at a time (fixed arq `job_id`) |

## Implementation

### Job Functions (`app/cron/jobs.py`, `app/cron/legacy_fcm.py`, and `app/cron/live_push.py`)

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
