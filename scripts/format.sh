#!/usr/bin/env bash
set -ex

# If argument was given then format only that file, else format entire app
if [[ -z "$path" ]]; then
    path="app"
fi

# Format
ruff format ${path}