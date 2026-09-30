"""Purge cached payloads after a schema change, as part of a deploy.

Cached values are this app's own JSON and are validated strictly on read, so a deploy
that changes a cached model leaves entries that fail validation and 500 until the cron
rewrites the cache (real incident: ``MatchWithDetails data.N.number Field required`` for
about three minutes of match-detail 500s). The deploy therefore compares a sha256
signature of the cached models' JSON schemas against the one stored in Redis under
``vlrgg:cache:schema`` and deletes this app's cache keys only when the signature changed
(a missing signature counts as changed). An unchanged schema leaves the warm cache
untouched, so an ordinary redeploy, even mid-match, is a no-op.

The purge runs after the new code is pulled and before the service reloads, so only the
outgoing worker can still write an old-schema entry, and short TTLs and cron intervals
bound how long such an entry survives. Losing a purged entry just forces a refetch or
republish within the normal intervals. Deletion is by the patterns below only — the same
Redis also holds the arq job queue and push delivery markers, so FLUSHALL/FLUSHDB must
never be used, and keys whose loss has a user-visible side effect are excluded on purpose.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import redis.asyncio as redis
from redis.exceptions import RedisError

from app import constants, schemas
from app.core.config import settings
from app.schemas.matches import VideoScore

DELETE_BATCH = 500  # keys per DEL call; the purge stays in the hundreds at most

# Keys this app writes whose payloads are validated on read. Add a new cached key here.
PURGE_PATTERNS = (
    "matches",  # match list (MatchListAdapter)
    "match:*",  # match details by ID (MatchWithDetails)
    "events",  # event list (EventListAdapter)
    "news",  # news list (NewsListAdapter)
    "rankings",  # rankings list (RankingListAdapter)
    "standings_*",  # VCT standings by year (Standings)
    "team:*",  # team pages (Team); the "team" id-map hash has no colon and is not matched
    "player:*",  # player pages (Player)
    constants.PUSH_DETAILS_KEY,  # tracked matches' last details (MatchWithDetails)
    constants.VIDEO_SCORE_KEY,  # broadcast tracker score (VideoScore)
)

# Deliberately not purged:
# - vlrgg:push:video_delivered: delivery marker; losing it makes the minute cron re-push
#   a match the video tracker already delivered, and its reader already drops a payload
#   that no longer validates instead of failing.
# - vlrgg:push:refresh:*: 60 s unchanged-refresh cooldown; losing it allows one duplicate
#   FCM refresh per follower.
# - vlrgg:push:fetch_failures:* and vlrgg:push:test_tick: counters, not payloads.
# - the "team" and "event" id-map hashes: plain name-to-id strings, never validated.


def schema_signature() -> str:
    """Hash the JSON schemas of every model whose serialized form is cached in Redis.

    ``vlrgg:push:details`` stores ``MatchWithDetails`` (covered as ``match_details``).
    ``VideoDelivery`` is not included: it is never purged, and its reader already
    degrades a stale payload to "not delivered".

    :return: A sha256 digest that changes when a cached model gains, loses, or retypes a field.
    """
    cached_schemas = {
        "matches": schemas.MatchListAdapter.json_schema(),
        "match_details": schemas.MatchWithDetails.model_json_schema(),
        "events": schemas.EventListAdapter.json_schema(),
        "news": schemas.NewsListAdapter.json_schema(),
        "rankings": schemas.RankingListAdapter.json_schema(),
        "standings": schemas.Standings.model_json_schema(),
        "team": schemas.Team.model_json_schema(),
        "player": schemas.Player.model_json_schema(),
        "video_score": VideoScore.model_json_schema(),
    }
    canonical = json.dumps(cached_schemas, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run(dry_run: bool) -> None:
    """Purge this app's cache keys when the cached-schema signature changed.

    :param dry_run: Report the branch and the keys each pattern matches without deleting
        or writing anything.
    :return: None.
    :raises SystemExit: If Redis is unreachable; a deploy must not continue when the
        cache's schema state is unknown.
    """
    if not settings.needs_redis:
        print("cache and live push are disabled; nothing to purge")
        return
    signature = schema_signature()
    client = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        stored = await client.get(constants.CACHE_SCHEMA_KEY)
        if stored == signature.encode():
            print("cached schemas unchanged; leaving the cache in place")
            return
        verb = "would purge" if dry_run else "purging"
        print(f"cached schema signature changed or is not stored; {verb} this app's cache keys")
        purged = 0
        for pattern in PURGE_PATTERNS:
            keys = [key async for key in client.scan_iter(match=pattern)]
            print(f"  {pattern}: {len(keys)} key(s)")
            if dry_run or not keys:
                continue
            for start in range(0, len(keys), DELETE_BATCH):
                purged += await client.delete(*keys[start : start + DELETE_BATCH])
        if dry_run:
            print(f"dry run: no keys deleted; would store signature {signature} under {constants.CACHE_SCHEMA_KEY}")
        else:
            await client.set(constants.CACHE_SCHEMA_KEY, signature)
            print(f"deleted {purged} key(s); stored schema signature under {constants.CACHE_SCHEMA_KEY}")
    except RedisError:
        print("redis unreachable; cannot verify the cached-schema signature, aborting", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await client.aclose()


def main() -> None:
    """Parse arguments and run the deploy-time cache purge check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report which branch the purge would take and the keys each pattern matches, without writing",
    )
    arguments = parser.parse_args()
    os.chdir(Path(__file__).resolve().parent.parent)  # the app's .env lives at the repo root
    asyncio.run(run(arguments.dry_run))


if __name__ == "__main__":
    main()
