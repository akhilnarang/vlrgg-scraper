#!/usr/bin/env bash

set -ex

mypy app
black app --check
isort --check-only app
flake8 app
