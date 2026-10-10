# PostgreSQL

The app stores subscriptions, scraped entities, and the ranking ledger in
PostgreSQL. It connects through `DATABASE_URL`; the default
`postgresql+asyncpg:///vlrgg` uses the local Unix socket with peer
authentication, so the service's OS user needs a role of the same name.

## Server setup

On the app host, as an admin:

```sh
sudo apt install postgresql
sudo -u postgres createuser ubuntu          # the systemd service's user
sudo -u postgres createdb -O ubuntu vlrgg
```

Startup runs the Alembic migrations, so the app creates its own tables.

## Cutover from SQLite

The app has to be stopped for the whole copy, because writes to SQLite after
the copy would be lost and the copy needs empty tables. The copy runs in one
transaction, so a failure leaves PostgreSQL empty and you can run it again.

```sh
systemctl --user stop vlrgg-scraper
git pull && uv sync
uv run python -m scripts.sqlite_to_postgres db.sqlite3   # prints each table's row count
systemctl --user start vlrgg-scraper
```

To roll back, check out the previous release and start it. The SQLite file is
opened read-only and left untouched.

## Backups

```sh
pg_dump -Fc vlrgg > backups/vlrgg-$(date -u +%Y%m%dT%H%M%SZ).dump
pg_restore --clean --if-exists -d vlrgg backups/<file>.dump
```

## Read-only access for contributors

Contributors connect with `psql` over Tailscale. PostgreSQL never listens on
a public interface, and each contributor has their own read-only role, so
you can revoke one person without touching the others.

### Network

1. In `postgresql.conf`, listen on the tailnet as well as the socket. A
   wildcard address keeps PostgreSQL from failing to start when it comes up
   before `tailscaled`; `pg_hba.conf` below still accepts only tailnet
   addresses.

   ```conf
   listen_addresses = '*'
   ```

2. In `pg_hba.conf`, accept password logins from the Tailscale ranges only,
   for the contributor group only:

   ```conf
   hostssl vlrgg +contributors 100.64.0.0/10        scram-sha-256
   hostssl vlrgg +contributors fd7a:115c:a1e0::/48  scram-sha-256
   ```

   Ubuntu's package already enables `ssl = on` with a snakeoil certificate.
   WireGuard encrypts the traffic, so TLS here is only defence in depth.

3. Share the host with each contributor through Tailscale node sharing
   (they keep their own tailnet), tag the host, and restrict them to the
   database port in the policy file:

   ```json
   "grants": [
     {"src": ["contributor@example.com"], "dst": ["tag:vlrgg"], "ip": ["tcp:5432"]}
   ]
   ```

4. Block 5432 on the host's other interfaces (`ufw deny 5432`, then
   `ufw allow in on tailscale0 to any port 5432`) in case `pg_hba.conf` is
   later loosened.

### Roles

Grant only the public VLR data. `clients`, `device_tokens`, `favorites`,
`live_activity_starts`, and `match_push_states` hold device push tokens and
per-device behaviour, so contributors never get access to them. Grants are
per table, so a new table stays private until you grant it here.

```sql
CREATE ROLE contributors NOLOGIN;
GRANT CONNECT ON DATABASE vlrgg TO contributors;
GRANT USAGE ON SCHEMA public TO contributors;
GRANT SELECT ON teams, players, events, matches, maps, ranking_results,
    team_elo, team_circuits, id_map, live_matches TO contributors;

-- One login per person, revoked with DROP ROLE. Settings are not inherited
-- from the group, so each login gets its own. Contributors share the
-- database with production, so their queries are kept short.
CREATE ROLE alice LOGIN PASSWORD '...' IN ROLE contributors CONNECTION LIMIT 3;
ALTER ROLE alice SET default_transaction_read_only = on;
ALTER ROLE alice SET statement_timeout = '30s';
ALTER ROLE alice SET idle_in_transaction_session_timeout = '60s';
```

A contributor can override these settings with `SET`, so the grants are the
real boundary; `default_transaction_read_only` only guards against mistakes.
A contributor then connects with:

```sh
psql "host=<tailscale-ip> dbname=vlrgg user=alice sslmode=require"
```
