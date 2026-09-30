# Caching System

The application uses Redis for caching to improve performance and reduce load on vlr.gg servers.

## Overview

- **Backend**: Redis (in-memory data structure store)
- **TTL**: Configurable expiration times
- **Async**: Non-blocking operations
- **Serialization**: JSON encoding/decoding

## Cache Keys

| Key Pattern | Description | TTL |
|-------------|-------------|-----|
| `rankings` | Current team rankings | 1 hour |
| `matches` | Match listings | 10 minutes |
| `match:{id}` | Match details | 30 seconds |
| `events` | Event listings | 1 hour |
| `news` | News articles | 1 hour |
| `standings_{year}` | VCT standings for year | 25 hours |
| `team:{id}:{completed_pages}` | Team pages | 1 minute |
| `player:{id}:{match_pages}` | Player pages | 1 minute |
| `vlrgg:push:details` | Each tracked match's last fetched details | 1 hour |
| `vlrgg:push:video_score` | Latest broadcast tracker score | 1 hour |

`vlrgg:push:video_delivered` (the last pushed video score) and `vlrgg:push:refresh:{match_id}`
(the cooldown on unchanged FCM refreshes) are markers with side effects, not payload caches,
so a purge leaves them alone.

## Implementation

### Cache Interface (`app/cache/cache.py`)

```python
class Cache:
    async def get(self, key: str) -> str | None:
        """Get value from cache"""

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        """Set value in cache with TTL"""

    async def delete(self, key: str) -> None:
        """Delete key from cache"""

    async def exists(self, key: str) -> bool:
        """Check if key exists"""
```

### Redis Implementation

- Uses `redis.asyncio.Redis` for async operations
- Connection pooling for efficiency
- Error handling for connection issues

### Usage in Endpoints

```python
@router.get("/rankings")
async def get_rankings() -> schemas.Ranking:
    if data := await cache.get("rankings"):
        return schemas.Ranking.model_validate(json.loads(data))

    result = await rankings.ranking_list()
    return result
```

## Background Updates

Cron jobs periodically refresh cache to ensure data freshness:

- **Rankings**: Every 30 minutes
- **Matches**: Every 5 minutes
- **Events**: Every 30 minutes
- **News**: Every 30 minutes
- **Standings**: Daily at midnight (current year only)

## Configuration

Environment variables:
- `REDIS_HOST`: Redis server hostname
- `REDIS_PASSWORD`: Redis password (if required)
- `REDIS_PORT`: Redis port (default 6379)

## Performance Benefits

- **Response Time**: Cached responses < 10ms vs scraped ~500ms
- **Server Load**: Reduces requests to vlr.gg
- **Scalability**: Multiple app instances share cache
- **Reliability**: Graceful degradation if vlr.gg is down

## Cache Invalidation

- **TTL Expiration**: Automatic cleanup
- **Schema changes**: Cached payloads are validated strictly on read, so a deploy that
  changes the schema of a cached model (`Match`, `MatchWithDetails`, `Event`, `NewsItem`,
  `Ranking`, `Standings`, `Team`, `Player`, `VideoScore`) must purge the application's
  keys by hand before the service reloads; an old-schema entry otherwise raises and 5xxs
  until it expires or its cron rewrites it. `ci.yml` fails on such a change as a
  reminder. From the repo root (pass the same `-h`/`-a` options to both `redis-cli`
  calls if Redis is not local):

  ```sh
  for p in rankings matches events news 'standings_*' 'match:*' 'team:*' 'player:*' vlrgg:push:details vlrgg:push:video_score; do redis-cli --scan --pattern "$p" | xargs -r redis-cli del; done
  ```

  Never `FLUSHALL`/`FLUSHDB`: the same Redis holds the arq job queue, and
  `vlrgg:push:video_delivered` and `vlrgg:push:refresh:*` must survive (losing either
  can duplicate a push). See [AGENTS.md: Caching](../AGENTS.md#caching).
- **Versioning**: Include version in keys for breaking changes

## Monitoring

Cache hit/miss ratios can be monitored via Redis `INFO` command or application metrics.

