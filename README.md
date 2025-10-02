# VLR.gg Scraper

An unofficial FastAPI-based scraper for [vlr.gg](https://www.vlr.gg), providing Valorant esports data in a machine-readable JSON format.

## Features

- **Comprehensive Data**: Scrapes events, matches, teams, players, rankings, standings, and news from vlr.gg
- **RESTful API**: FastAPI-powered endpoints with automatic OpenAPI documentation
- **Caching**: Redis-based caching for improved performance
- **Background Jobs**: Cron jobs for periodic data updates
- **Async Support**: Asynchronous HTTP requests for efficient scraping

## Architecture

The application follows a modular architecture:

- **API Layer** (`app/api/`): FastAPI routers and endpoints
- **Service Layer** (`app/services/`): Business logic and scraping functionality
- **Schema Layer** (`app/schemas/`): Pydantic models for data validation
- **Cache Layer** (`app/cache/`): Redis integration for caching
- **Core** (`app/core/`): Configuration and database connections
- **Cron** (`app/cron/`): Background job scheduling with arq

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/events` | List upcoming and completed events |
| `GET /api/v1/matches` | Match listings with filtering |
| `GET /api/v1/news` | Latest Valorant news |
| `GET /api/v1/player/{id}` | Player statistics and info |
| `GET /api/v1/rankings` | Current team rankings by region |
| `GET /api/v1/standings/{year}` | VCT standings for a specific year |
| `GET /api/v1/team/{id}` | Team details and matches |
| `GET /api/v1/search` | Search teams, players, and events |
| `GET /api/v1/version` | API version info |

See [API Documentation](docs/api.md) for detailed endpoint specs.

## Documentation

- [API Reference](docs/api.md) - Detailed endpoint specifications
- [Architecture](docs/architecture.md) - System design and components
- [Caching](docs/caching.md) - Redis caching implementation
- [Background Jobs](docs/cron.md) - Cron job scheduling
- [Standings API](docs/standings.md) - VCT standings implementation

## Quick Start

### Prerequisites

- Python 3.13+
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
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=app
```

## Configuration

Environment variables (see `app/core/config.py`):

- `REDIS_HOST`: Redis server host
- `REDIS_PASSWORD`: Redis password
- `INTERNAL_API_KEY`: API key for internal endpoints
- `TIMEZONE`: Server timezone
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to Firebase credentials (for notifications)

## Deployment

### Docker

```bash
# Build image
docker build -t vlrgg-scraper .

# Run container
docker run -p 8000:8000 vlrgg-scraper
```

### Production

Use `uvicorn` or `gunicorn` for production deployment. See `scripts/start.sh` for an example.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Submit a pull request

## License

This project is for educational purposes only. Respect vlr.gg's terms of service and rate limits.

## Contact

For questions or issues: [me@akhilnarang.dev](mailto:me@akhilnarang.dev)