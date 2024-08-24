FROM        python:3.12-slim AS build
COPY        --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY        . /app
WORKDIR     /app
RUN         uv sync --frozen --no-cache

FROM        python:3.12-slim
RUN         groupadd -g 999 vlrscraper && useradd -r -u 999 -g vlrscraper vlrscraper
RUN         mkdir /app && chown vlrscraper:vlrscraper /app
WORKDIR     /app
COPY        --chown=vlrscraper:vlrscraper --from=build app/.venv ./.venv
COPY        --chown=vlrscraper:vlrscraper . .
EXPOSE      8000
USER        vlrscraper
RUN         uv run gunicorn -k uvicorn.workers.UvicornWorker --workers=$(( $(nproc) * 2 + 1 )) --bind=0.0.0.0 app.main:app

