#!/bin/sh
# Deploy: pull the latest, sync the venv, and restart the service.
#
# `uv sync` runs here rather than in the unit so startup stays deterministic
# and never tries to mutate the environment.
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

uv=$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")

git pull --ff-only
"$uv" sync --locked
systemctl --user restart vlrgg-scraper.service
systemctl --user --no-pager status vlrgg-scraper.service | head -n 8
