#!/usr/bin/env bash

set -ex

# If argument was given then lint only that file, else lint entire app
if [[ -z "$path" ]]; then
    path="app"
fi

# Lint
mypy $path --explicit-package-bases
black $path --check
isort --check-only $path
flake8 $path
