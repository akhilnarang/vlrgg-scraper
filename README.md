# VLR.gg Scraper

An unofficial FastAPI-based scraper for [vlr.gg](https://www.vlr.gg), providing Valorant esports data in a machine-readable JSON format.

## Features

- **Comprehensive Data**: Scrapes events, matches, teams, players, rankings, standings, and news from vlr.gg
- **RESTful API**: FastAPI-powered endpoints with automatic OpenAPI documentation
- **Caching**: Redis-based caching for improved performance
- **Live match updates**: Optional APNs Live Activities and FCM topic messages for matches that are running
- **Background Jobs**: Cron jobs for periodic data updates
- **Async Support**: Asynchronous HTTP requests for efficient scraping

## Documentation

The running server documents every endpoint at `/docs` (Swagger UI), `/redoc`, and
`/openapi.json`. The guides in `docs/` cover the rest:

- [API Reference](docs/api.md): authentication, live match updates, and errors
- [Architecture](docs/architecture.md): components, data flow, and deployment
- [Caching](docs/caching.md): Redis caching
- [Background Jobs](docs/cron.md): cron schedules
- [News Media](docs/news-media.md): news article content and media
- [Standings](docs/standings.md): VCT standings
- [Testing](docs/testing.md): test layout and commands

## Quick Start

### Prerequisites

- Python 3.14+
- [uv](https://astral.sh/uv) for dependency management

### Installation

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install dependencies
git clone <repo-url>
cd vlrgg-scraper
uv sync
```

### Development

```bash
# Run development server with auto-reload
uv run fastapi dev

# Access API docs at http://localhost:8000/docs
# Access API at http://localhost:8000/api/v1/
```

### Testing

```bash
uv run pytest -m "not live_golden and not live_health"
```

Live checks against vlr.gg and more commands are in [docs/testing.md](docs/testing.md).

## Configuration

Environment variables (see `app/core/config.py`):

- `REDIS_HOST`: Redis server host
- `REDIS_PASSWORD`: Redis password
- `ENABLE_CACHE`: Cache VLR responses in Redis and run the cache crons (default `false`)
- `ENABLE_ID_MAPPING`: Resolve team and event IDs in match lists through Redis; requires `ENABLE_CACHE`, and enables the favorites endpoint and players cron (default `false`)
- `ENABLE_LIVE_PUSH`: Enable the live push cron and client endpoints (default `false`)
- `APNS_CREDENTIALS_FILE`: Path to the APNs credential JSON file
- `DATABASE_URL`: SQLite database (default `sqlite+aiosqlite:///db.sqlite3`)
- `INTERNAL_API_KEY`: API key for internal endpoints
- `TIMEZONE`: Server timezone
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to Firebase credentials (for notifications)

Startup applies the Alembic migrations automatically. Run
`uv run scripts/backup.py [backup-path]` for an online SQLite backup.

## Deployment

### Docker

```bash
# Build image
docker build -t vlrgg-scraper .

# Run container
docker run -p 8000:8000 vlrgg-scraper
```

### Production

```bash
# First install: sync dependencies, install and start the systemd user service
./scripts/install-systemd-user.sh

# Later deploys: pull, reinstall the unit, and restart
./scripts/deploy.sh
```

See [Architecture: Deployment](docs/architecture.md#deployment) for how the service runs.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Submit a pull request

## License

This project is for educational purposes only. Respect vlr.gg's terms of service and rate limits.

## Contact

For questions or issues: [me@akhilnarang.dev](mailto:me@akhilnarang.dev)
