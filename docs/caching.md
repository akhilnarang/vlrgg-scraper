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
| `matches` | Match listings | 5 minutes |
| `events` | Event listings | 30 minutes |
| `news` | News articles | 30 minutes |
| `standings_{year}` | VCT standings for year | 1 hour |

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
- **Manual Flush**: `FLUSHDB` command for emergencies
- **Versioning**: Include version in keys for breaking changes

## Monitoring

Cache hit/miss ratios can be monitored via Redis `INFO` command or application metrics.

