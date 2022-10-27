#!/usr/bin/env bash

set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt update --yes
apt install build-essential --yes
pip --no-cache-dir install poetry
poetry config virtualenvs.create false
poetry install
rm -rf $(poetry config cache-dir)
apt autoremove build-essential --yes
rm -rf /var/apt/lib/lists
