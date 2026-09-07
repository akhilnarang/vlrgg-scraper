#!/bin/sh
# Deploy: pull the latest, install the unit/dependencies, and restart the service.
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

git pull --ff-only
exec "$root/scripts/install-systemd-user.sh"
