# Database

The application keeps durable state in SQLite through SQLAlchemy's async API
(`sqlite+aiosqlite`). Redis remains the L1 cache for scraped API payloads (see
[caching.md](caching.md)); SQLite holds the state that must survive restarts:
live-update subscriptions, the team-ranking ledger, and the live-match details
the video push falls back to when Redis is cold.

## Engine and lifecycle

- `DATABASE_URL` (default `sqlite+aiosqlite:///db.sqlite3`) names the database
  file; its parent directory is created at startup.
- `app/db/engine.py` applies four pragmas to every connection:
  `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, and
  `foreign_keys=ON`. WAL lets API reads run while a cron writes; the busy
  timeout waits for a held write lock instead of failing at once; foreign keys
  enforce the `ON DELETE CASCADE` rules.
- `app/db/lifecycle.py` runs the Alembic migrations to head on startup, then
  opens the shared session factory (`subscription_sessions`). Migrations live in
  `alembic/versions/`; every schema change needs one.
- JSON payloads use `app/db/types.py:JSONB`, which binds through SQLite's
  `jsonb()` and reads back through `json()` so the column round-trips as a dict.
- Back up the live database with `uv run scripts/backup.py [backup-path]`. The
  copy includes committed WAL changes and is verified with
  `PRAGMA integrity_check` before it replaces the destination.

## Conventions

- Stores never commit. The caller owns the transaction: a request dependency
  commits on success and rolls back on error, or a cron opens one transaction
  (for push state, one per match) and commits before any provider call.
- Never hold the SQLite write lock across network I/O. Read tokens or state,
  commit, then call APNs, FCM, or VLR; commit rows before sending anything.
- Engine setup belongs in `app/db/`, stores in `app/services/`, cron handlers in
  `app/cron/`.

## Tables

### Live updates and push

| Table | Purpose |
| --- | --- |
| `clients` | One row per installed client UUID. `live_updates` stores the client's opt-in flag: `false` stops all sends, `null`/`true` do not. |
| `device_tokens` | The client's single APNs push-to-start or FCM registration token and its platform. Cascades on client delete. |
| `favorites` | One row per client interest (`entity_type` + `entity_id`), unique per client and entity. |
| `match_push_states` | Per tracked match: the APNs broadcast `channel_id` and the last state sent (`last_state_json`, serialized without `observed_at`). Used to detect changes and to build a final state when the match page disappears. |
| `live_activity_starts` | Records that a client was already sent a start for a match, so a start happens at most once per client and match. |
| `live_matches` | Fallback copies of each tracked match's VLR details and tracker score, used when the Redis cache is cold. Described in detail below. |

### Scraped data and ranking ledger

| Table | Purpose |
| --- | --- |
| `teams` | VLR teams as last seen by any scrape, plus the Riot fields they are matched to. |
| `players` | Last scraped VLR player page payload and the player's current team, refreshed by the favorite-players cron. |
| `id_map` | Copy of the Redis team/event name→VLR ID hashes, loaded by `scripts/backfill_id_map.py` where the app runs. |
| `events` | Events whose matches are in the ledger, with their circuit and when the circuit was last checked. |
| `matches` | Completed series in the ranking ledger. Team IDs are NULL for TBD; `patch` is VLR's event patch; `source` records the provider. |
| `maps` | Played maps of a ledger match, oriented to the match's team order (`map_index` is the 0-based series position). |
| `ranking_results` | One Elo-moving result per series or map, with both teams' ratings before it, team A's delta, and the winner. Unique per `(match_id, scope, map_index)`; a series row uses `map_index` `-1`. |
| `team_elo` | Current series and map rating, win/loss counters, and first/last played dates per team, plus its primary region. Ratings never decay. |
| `team_circuits` | Rated match count and last played date per team and circuit. |

## Live-match fallback (`live_matches`)

The video push normally resolves the tracked match from the
`valesports:vlr:details` Redis payload. When that cache is cold (a restart, TTL
expiry, or a match the last cron run dropped), it falls back to the SQLite copy,
so the tracker's score still advances and ends the right match.

| Column | Meaning |
| --- | --- |
| `match_id` | Primary key; the VLR match ID. |
| `detail` | Last fetched `MatchWithDetails` payload as JSONB. |
| `detail_fetched_at` | When those details were fetched (Unix seconds). |
| `video` | Last tracker score received for the match as JSONB. |
| `video_received_at` | When that score was received (Unix seconds). |
| `updated_at` | When either side of the row last changed (Unix seconds). |

How it behaves:

- The minute cron writes every successfully fetched match's details after its
  Redis write (`upsert_live_detail`), one transaction for the run's matches.
  The video push stores the tracker's score when it resolves a match
  (`upsert_live_video`). Each upsert updates only its own columns, so details
  and score never overwrite each other. The synthetic test match is never
  stored.
- `resolve_video_match_with_fallback` tries Redis first, then reads the SQLite
  rows. Rows whose event status is final are excluded even if a failed cleanup
  left them behind, so the fallback can never resolve a finished match,
  re-announce a final, or resurrect one.
- On a terminal delivery, or when a match is dropped because its state shows no
  play, the cron first clears `detail` and `detail_fetched_at` in one commit
  and then deletes the row in another. The retirement alone makes the match
  unresolvable, so a failed delete cannot leave live details for the video push
  to pick up.
- A failed write to `live_matches` is logged and skipped; the SQLite copy is
  only the fallback, so it must never block a push.

## Schema changes

A deploy that changes the schema of a Redis-cached model must purge the
application cache by hand, because cached payloads are validated strictly on
read (see [caching.md](caching.md#cache-invalidation) and
[AGENTS.md: Caching](../AGENTS.md#caching)). SQLite is migrated automatically at
startup, so a migration must keep an existing database upgradable: add columns
nullable or with a server default.
