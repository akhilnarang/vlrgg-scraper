#!/usr/bin/env bash

pip --no-cache-dir install poetry
poetry config virtualenvs.create false
poetry install
rm -rf $(poetry config cache-dir)
