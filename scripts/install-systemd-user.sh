#!/bin/sh
# Install the vlrgg-scraper systemd user unit (one-time per host).
#
# Copies deploy/systemd/vlrgg-scraper.service into the user unit directory,
# syncs the venv (the unit runs --no-sync), enables it, and starts it. Re-run
# this script to pick up unit changes. Stop any process already using
# gunicorn.sock before installation so the service can bind it.
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
unit_name=vlrgg-scraper.service
unit_src=$root/deploy/systemd/$unit_name
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

uv=$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")
[ -x "$uv" ] || { echo "uv not found at $uv" >&2; exit 1; }

install -d -m 0755 "$unit_dir"
install -m 0644 "$unit_src" "$unit_dir/$unit_name"

cd "$root"
"$uv" sync --locked

systemctl --user daemon-reload
systemctl --user enable --now "$unit_name"
systemctl --user --no-pager status "$unit_name" | head -n 8
