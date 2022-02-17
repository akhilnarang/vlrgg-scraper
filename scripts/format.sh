#!/bin/sh -e
set -x

# If argument was given then format only that file, else format entire app
path=${1:-app}

# Format
autoflake --remove-all-unused-imports --recursive --remove-unused-variables --in-place "${path}" --exclude=__init__.py
black "${path}"
isort "${path}"
