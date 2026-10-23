# Database

The application keeps durable state in PostgreSQL through SQLAlchemy's async API
(`postgresql+asyncpg`). Redis remains the L1 cache for scraped API payloads (see
[caching.md](caching.md)); PostgreSQL holds the state that must survive restarts:
live-update subscriptions, the team-ranking ledger, and the live-match details
the video push falls back to when Redis is cold.

## Engine and lifecycle

- `DATABASE_URL` (default `postgresql+asyncpg:///vlrgg`) names the database.
  Server setup, the SQLite cutover, backups, and contributor access are in
  [postgres.md](postgres.md).
- `app/db/lifecycle.py` runs the Alembic migrations to head on startup, then
  opens the shared session factory (`subscription_sessions`). Migrations take an
  advisory lock, so workers that start together wait for each other. They live
  in `alembic/versions/`; every schema change needs one.
- JSON payloads use PostgreSQL `JSONB` with `none_as_null=True`, so a cleared
  column is SQL `NULL`, not JSON `null`.

## Conventions

- Stores never commit. The caller owns the transaction: a request dependency
  commits on success and rolls back on error, or a cron opens one transaction
  (for push state, one per match) and commits before any provider call.
- Never hold a transaction or row lock across network I/O. Read tokens or state,
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
| `riot_matches` | Games from Riot's esports match API, stored whole (`data`) as soon as Riot lists them. `vlr_match_id`, `map_number`, `linked_at` and `link` stay NULL until the game is linked to exactly one VLR map; a partial unique index over `(vlr_match_id, map_number)` where `vlr_match_id` is not null keeps one game per map. |
| `riot_web_matches` | Series from Riot's web persisted service, one row per Riot series, with the VLR match it was linked to (`vlr_match_id`, `vlr_event_id`). The link is written once and never rewritten by a cron. |
| `riot_web_tournaments` | Each Riot tournament's parsed standings layout (`standings` JSONB), and when it was fetched. Converted to the public bracket shape on read. |

### Riot web matches (`riot_web_matches`)

This is a different provider and a different granularity from `riot_matches`: one row per Riot
*series* (the `getSchedule` `match.id`, also `getEventDetails`'s `event.id`), not per game.
`teams` is JSONB holding the two team names the schedule last showed; the schedule payload
has no team IDs, so there is no id column, and nothing else about the teams is read.
`vlr_match_id` stays NULL until the schedule cron finds one unambiguously matching VLR match,
and the store updates only while it is NULL, so a rerun cannot rewrite a link. `tournament_id` comes from `getEventDetails` of the
linked series and is what maps a VLR event to Riot's `getStandings`; the brackets cron fills it
for matches near the current date, while NULL while it is unknown. Clear a wrong link by hand as
shown in [cron.md](cron.md#job-schedule); the next run may link it again.

The two Riot web tables are created by the single revision `625f004fc4c9`, which also makes a
`riot_matches` game exist before it is linked. The revision has never shipped, so it is edited in
place rather than superseded; its downgrade refuses to run while an unlinked or partial
`riot_matches` row exists (see [Riot game archive](#riot-game-archive-riot_matches)).

### Riot web tournaments (`riot_web_tournaments`)

`standings` holds the parsed `data.standings[0]` layout — its stages, sections, columns, cells,
matches and slots — so the public bracket shape is built at read time and a converter fix needs
no refetch; the tournament's own name and window are inside that payload and are not duplicated
in columns. `fetched_at` is the last successful
fetch; `standings` is NULL when Riot had no layout, and the route skips that tournament for 24
hours instead of asking again. A stored layout that no longer validates is refetched once and,
if Riot fails, logged; a schema change degrades to fewer sections rather than an error. The table
is not strictly cached in Redis: the converted response is cached separately for 60 seconds.

## Riot game archive (`riot_matches`)

Riot's esports match API lists each tournament-server game for about 12 hours and
keeps no history, so `app/cron/riot_matches.py` archives every completed game as
soon as it sees it, before attempting any VLR link. The row is keyed by `riot_id`
and upserted, so a rerun updates the same row instead of adding another. `data`
holds Riot's whole payload (round results and `matchInfo`) and is not NULL.
Only a completed game with round results is archived: an empty completed payload
is skipped so a later run retries it while Riot still lists it.

The migration that made the VLR link optional (`625f004fc4c9`) aborts its
downgrade while unlinked or partial rows exist, rather than deleting archive
games that Riot can no longer return; export or prune those rows deliberately
first.

Linking is a separate, best-effort step over rows whose `vlr_match_id` is still
NULL. It sets `vlr_match_id`, `map_number`, `linked_at` and the model's `link`
verdict only when exactly one VLR map accepts the game. A game with no VLR map
yet, an unknown map codename, or unreadable sides keeps those columns NULL but
stays archived; a later run retries linking while Riot still lists it. The
partial unique index over `(vlr_match_id, map_number)` where `vlr_match_id` is
not null prevents two games, such as a voided game and its replay, from claiming
the same map.

## Live-match fallback (`live_matches`)

The video push normally resolves the tracked match from the
`valesports:vlr:details` Redis payload. When that cache is cold (a restart, TTL
expiry, or a match the last cron run dropped), it falls back to the database copy,
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
- `resolve_video_match_with_fallback` tries Redis first, then reads the database
  rows. Rows whose event status is final are excluded even if a failed cleanup
  left them behind, so the fallback can never resolve a finished match,
  re-announce a final, or resurrect one.
- On a terminal delivery, or when a match is dropped because its state shows no
  play, the cron first clears `detail` and `detail_fetched_at` in one commit
  and then deletes the row in another. The retirement alone makes the match
  unresolvable, so a failed delete cannot leave live details for the video push
  to pick up.
- A failed write to `live_matches` is logged and skipped; the database copy is
  only the fallback, so it must never block a push.

## Schema changes

A deploy that changes the schema of a Redis-cached model must purge the
application cache by hand, because cached payloads are validated strictly on
read (see [caching.md](caching.md#cache-invalidation) and
[AGENTS.md: Caching](../AGENTS.md#caching)). The database is migrated automatically at
startup, so a migration must keep an existing database upgradable: add columns
nullable or with a server default.
