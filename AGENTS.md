# Agents and Commands

This file documents useful commands and agents for development and maintenance of the vlrgg-scraper project.

## Project Overview

vlrgg-scraper is a web scraper for VLR.gg, a Valorant esports website. It provides APIs for fetching news, matches, events, teams, players, rankings, and standings.

- List endpoints (e.g., news list, matches list) are cached via a cron job.
- Fetch-by-ID endpoints (e.g., specific news article, match details) are not cached and fetched on-demand.
- Optional feature: Maintain a mini database in Redis (enabled via env var) for normalized team name-to-ID mappings and other data.

## Architecture

- **API Layer**: FastAPI routers with Pydantic validation and OpenAPI docs.
- **Service Layer**: Scraping logic with httpx and BeautifulSoup parsing, using Redis semaphore for rate limiting.
- **Schema Layer**: Pydantic models for type-safe data structures.
- **Cache Layer**: Redis for in-memory caching with TTL and per-request client optimization.
- **Cron Layer**: arq for background job scheduling (data updates).
- **Core**: Configuration, connections, semaphore, and utilities.

## Caching

Cache keys and TTLs:
- `rankings`: 1 hour
- `matches`: 5 minutes
- `events`: 30 minutes
- `news`: 30 minutes
- `standings_{year}`: 1 hour

Background cron jobs refresh cache periodically.

## Cron Jobs

Scheduled background jobs using arq:
- **Rankings**: Every 30 minutes
- **Matches**: Every 5 minutes
- **Events**: Every 30 minutes
- **News**: Every 30 minutes
- **Standings**: Daily at 00:00 (current year)
- **FCM Notifications**: Every 15 minutes (if configured)

## Environment Variables

- `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PORT`: Redis connection
- `ENABLE_CACHE`: Enable caching and cron jobs
- `ENABLE_ID_MAP_DB`: Enable mini DB for team mappings
- `SENTRY_DSN`: Error tracking
- `GOOGLE_APPLICATION_CREDENTIALS`: FCM notifications
- `API_KEYS`: Authentication
- `TIMEZONE`: Default timezone
- `INTERNAL_API_KEY`: Internal endpoints

## Testing
- Run all tests: `uv run pytest`
- Run specific test file: `uv run pytest tests/test_news.py`
- Run specific test: `uv run pytest tests/test_news.py::test_news_list`
- Run with verbose output: `uv run pytest -v`

## Linting and Type Checking
- Lint code: `./scripts/lint.sh` (runs ty and ruff check)
- Type check only: `ty check app/`
- Lint only: `uv run ruff check app/`

## Formatting
- Format code: `./scripts/format.sh` (runs ruff format)
- Format specific file: `uv run ruff format app/services/news.py`

## Starting the Application
- Start the app: `./scripts/start.sh` (production with gunicorn)
- Development: `uv run fastapi dev app/main.py`

## Dependency Management
- Install/sync dependencies: `uv sync`
- Add a dependency: `uv add <package>`
- Remove a dependency: `uv remove <package>`

## Database Migrations
- Create new migration: `uv run alembic revision --autogenerate -m "Description"`
- Apply migrations: `uv run alembic upgrade head`
- View migration status: `uv run alembic current`
- Downgrade: `uv run alembic downgrade <revision>`

## Docker
- Build Docker image: `docker build -t vlrgg-scraper .`
- Run with Docker: `docker run -p 8000:8000 vlrgg-scraper`

## CI/CD
- GitHub Actions workflow runs pytest on push/PR to master branch.

## Monitoring and Logging
- Structured logging with Rich handler.
- Sentry integration for error tracking (if DSN provided).
- Health checks and metrics via FastAPI middleware.
- FCM notifications for upcoming matches (if credentials provided).

## Development Workflow
1. Local development: `uv run fastapi dev` with auto-reload
2. Testing: `uv run pytest` with coverage
3. Linting: `./scripts/lint.sh` and `ty check`
4. Formatting: `./scripts/format.sh`
5. Documentation: Auto-generated OpenAPI docs at `/docs`

## Deployment
- Containerized with Docker.
- Production: gunicorn with Uvicorn workers.
- Supports horizontal scaling and Redis clustering.

## Notes
- Use `uv` for Python dependency management.
- Ensure Python 3.13+ is used.
- Async operations throughout for performance.
- Respect vlr.gg rate limits to avoid bans.
- For any new commands or agents, add them here.