#!/bin/sh
# Install the vlrgg-scraper systemd user unit (one-time per host).
#
# Copies deploy/systemd/vlrgg-scraper.service into the user unit directory,
# syncs the venv, and enables it. A running service with an unchanged unit is
# reloaded (new worker, same socket); otherwise it is restarted.
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

action=restart
if cmp -s "$unit_src" "$unit_dir/$unit_name" && systemctl --user is-active --quiet "$unit_name"; then
    action=reload
fi

install -d -m 0755 "$unit_dir"
install -m 0644 "$unit_src" "$unit_dir/$unit_name"
systemctl --user daemon-reload
systemctl --user enable "$unit_name"
systemctl --user "$action" "$unit_name"
systemctl --user --no-pager status "$unit_name" | head -n 8
