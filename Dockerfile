FROM        python:3.10-slim as build
WORKDIR     /app
COPY        pyproject.toml poetry.lock scripts/setup.sh ./
RUN         bash setup.sh

FROM        python:3.10-slim
RUN         groupadd -g 999 vlrscraper && useradd -r -u 999 -g vlrscraper vlrscraper
RUN         mkdir /app && chown vlrscraper:vlrscraper /app
WORKDIR     /app
COPY        --chown=vlrscraper:vlrscraper --from=build app/venv ./venv
COPY        --chown=vlrscraper:vlrscraper . .
EXPOSE      8000
USER        vlrscraper
CMD         ["bash", "scripts/start.sh"]
