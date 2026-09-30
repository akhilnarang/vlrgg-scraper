#!/bin/sh
# Deploy: pull the latest, purge cache entries whose schema changed, and reload/restart
# the service. The purge is conditional: a schema change with a warm cache otherwise
# serves entries that fail strict validation and 500 (MatchWithDetails data.N.number
# Field required) until the cron rewrites the cache. See scripts/purge_cache.py.
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

git pull --ff-only

uv=$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")
[ -x "$uv" ] || { echo "uv not found at $uv" >&2; exit 1; }

# Fails the deploy if Redis is unreachable, so the service never reloads onto a cache
# whose schema state is unknown.
"$uv" run --locked --no-dev python -m scripts.purge_cache

exec "$root/scripts/install-systemd-user.sh"
