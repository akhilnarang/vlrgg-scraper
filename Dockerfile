FROM        python:3.12-slim
COPY        --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
RUN         groupadd -g 999 vlrscraper && useradd -m -r -u 999 -g vlrscraper vlrscraper
RUN         mkdir /app && chown vlrscraper:vlrscraper /app
COPY        . /app
WORKDIR     /app
USER        vlrscraper
RUN         uv sync --frozen --no-cache --no-dev
EXPOSE      8000
CMD         ["uv", "run", "fastapi", "dev"]

