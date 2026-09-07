#!/bin/sh
# Install the vlrgg-scraper systemd user unit (one-time per host).
#
# Copies deploy/systemd/vlrgg-scraper.service into the user unit directory,
# syncs the venv, enables it, and restarts it to apply unit changes.
# Stop any independently managed process using gunicorn.sock before installation.
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
unit_name=vlrgg-scraper.service
unit_src=$root/deploy/systemd/$unit_name
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

uv=$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")
[ -x "$uv" ] || { echo "uv not found at $uv" >&2; exit 1; }

cd "$root"
"$uv" sync --locked --no-dev

install -d -m 0755 "$unit_dir"
install -m 0644 "$unit_src" "$unit_dir/$unit_name"
systemctl --user daemon-reload
systemctl --user enable "$unit_name"
systemctl --user restart "$unit_name"
systemctl --user --no-pager status "$unit_name" | head -n 8
