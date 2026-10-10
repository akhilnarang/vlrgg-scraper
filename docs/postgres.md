# PostgreSQL

The app stores subscriptions, scraped entities, and the ranking ledger in
PostgreSQL. It connects through `DATABASE_URL`; the default
`postgresql+asyncpg:///vlrgg` uses the local Unix socket with peer
authentication, so the service's OS user needs a role of the same name.

## Server setup

PostgreSQL runs from the apt binaries under the systemd user unit
`deploy/systemd/postgres.service`, not as the apt `postgresql` service, so a
package upgrade never restarts it. After an upgrade, restart it when convenient
with `systemctl --user restart postgres`. The data directory is `~/pgdata`.
PostgreSQL does not listen on TCP; its only socket is in the checkout, next to
`gunicorn.sock`.

On the app host, as the service user:

```sh
sudo apt install postgresql-18 postgresql-client-18
sudo pg_dropcluster 18 main --stop                 # the empty cluster apt created
sudo systemctl mask postgresql postgresql@18-main
# keep a later major version from creating a cluster that takes port 5432
sudo sed -i 's/^#\?\s*create_main_cluster\s*=.*/create_main_cluster = false/' /etc/postgresql-common/createcluster.conf

/usr/lib/postgresql/18/bin/initdb -D ~/pgdata --auth-local=peer --auth-host=scram-sha-256 -E UTF8 --locale=C.UTF-8
printf "listen_addresses = ''\nunix_socket_directories = '%s'\n" ~/vlrgg-scraper >> ~/pgdata/postgresql.conf
install -m 0644 deploy/systemd/postgres.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now postgres
createdb -h ~/vlrgg-scraper vlrgg
```

Add `export PGHOST=~/vlrgg-scraper` to the shell profile so `psql`, `pg_dump` and
`createdb` find the socket. The app needs the socket in its URL, in `.env`:

```sh
DATABASE_URL=postgresql+asyncpg:///vlrgg?host=/home/<user>/vlrgg-scraper
```

Startup runs the Alembic migrations, so the app creates its own tables.

When Ubuntu moves to a new major version, the old binaries stay installed;
upgrade the data directory with `pg_upgrade` and point the unit at the new
binaries.

## Cutover from SQLite

The app has to be stopped for the whole copy, because writes to SQLite after
the copy would be lost and the copy needs empty tables. The copy runs in one
transaction, so a failure leaves PostgreSQL empty and you can run it again.

```sh
systemctl --user stop vlrgg-scraper
git pull && uv sync
# add DATABASE_URL to .env (Server setup) first; the copy reads it too
uv run python -m scripts.sqlite_to_postgres db.sqlite3   # prints each table's row count
psql vlrgg   # run the contributor GRANTs below, now that the tables exist
systemctl --user start vlrgg-scraper
```

To roll back, stop the app, check out the previous release, delete the
`DATABASE_URL` line from `.env` (the old release defaults to `db.sqlite3`), run
`uv sync` to reinstall its SQLite driver, and start it. The SQLite file is
opened read-only and left untouched.

This is only safe before PostgreSQL takes writes: tokens, favorites and Live Activity
start markers written after the cutover are lost, and a lost start marker can repeat a push.

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

nginx's `stream` module (`apt install libnginx-mod-stream`) proxies port 5432
on the host's Tailscale address to the PostgreSQL socket, so nothing listens
on a public interface. nginx binds that address at startup, so it has to start
after `tailscaled` (`Type=notify`, ready once the node is up). As
`/etc/systemd/system/nginx.service.d/tailscale.conf`, then `systemctl daemon-reload`:

```ini
[Unit]
After=tailscaled.service
Wants=tailscaled.service
```

In `/etc/nginx/nginx.conf`, at the top level:

```nginx
stream {
	include /etc/nginx/streams-enabled/*.conf;
}
```

and in `/etc/nginx/streams-enabled/vlrgg-postgres.conf`, with the host's
`tailscale ip -4`:

```nginx
server {
	listen <tailscale-ip>:5432;
	proxy_pass unix:/home/<user>/vlrgg-scraper/.s.PGSQL.5432;
}
```

Then `sudo nginx -t && sudo systemctl reload nginx`.

Proxied connections reach PostgreSQL over the socket as `www-data`, so the
peer rule would reject them. In `pg_hba.conf`, above `local all all peer`,
let the contributor group in by password:

```conf
local   vlrgg           +contributors                           scram-sha-256
```

then `systemctl --user reload postgres`, since a running server only reads
`pg_hba.conf` on reload.

WireGuard encrypts the traffic, so the connection does not use TLS.

Share the host with each contributor through Tailscale node sharing (they
keep their own tailnet), tag the host, and restrict them to the database
port in the policy file:

```json
"grants": [
  {"src": ["contributor@example.com"], "dst": ["tag:vlrgg"], "ip": ["tcp:5432"]}
]
```

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
psql "host=<tailscale-ip> dbname=vlrgg user=alice"
```
