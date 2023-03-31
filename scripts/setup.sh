#!/usr/bin/env bash

set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt update -yq
apt install build-essential curl -yq
curl -sSL https://install.python-poetry.org | python3 -

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
source ./venv/bin/activate

/root/.local/bin/poetry install --without=dev