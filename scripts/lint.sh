#!/usr/bin/env bash

set -ex

# If argument was given then lint only that file, else lint entire app
if [[ -z "$path" ]]; then
    path="app"
fi

# Lint
mypy $path --explicit-package-bases
ruff check $path