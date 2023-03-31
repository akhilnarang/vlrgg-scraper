#!/usr/bin/env bash

cd /app || exit
./venv/bin/gunicorn -k uvicorn.workers.UvicornWorker --workers=$(( $(nproc) * 2 + 1 )) --bind=0.0.0.0 app.main:app

