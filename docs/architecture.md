# Architecture

This document describes the system architecture and design decisions.

## Overview

The VLR.gg scraper is built as a FastAPI application that provides a REST API for Valorant esports data scraped from vlr.gg. The system emphasizes performance, reliability, and maintainability.

## Core Components

### API Layer (`app/api/`)
- **FastAPI Routers**: Define endpoints and handle HTTP requests/responses
- **Pydantic Validation**: Automatic request/response validation
- **OpenAPI Generation**: Automatic API documentation via Swagger/ReDoc

### Service Layer (`app/services/`)
- **Scraping Logic**: HTTP requests to vlr.gg using httpx
- **HTML Parsing**: BeautifulSoup for extracting data from HTML
- **Business Logic**: Data transformation and processing

### Schema Layer (`app/schemas/`)
- **Pydantic Models**: Type-safe data models with validation
- **Serialization**: JSON serialization/deserialization
- **API Contracts**: Define request/response structures

### Cache Layer (`app/cache/`)
- **Redis Integration**: In-memory caching for performance
- **TTL Support**: Configurable cache expiration
- **Async Operations**: Non-blocking cache operations

### Core (`app/core/`)
- **Configuration**: Environment-based settings management
- **Connections**: Database and external service connections
- **Utilities**: Helper functions for common operations

### Cron (`app/cron/`)
- **Background Jobs**: Periodic data updates using arq
- **Scheduling**: `worker.py` schedules `jobs.py`, `legacy_fcm.py`, and `live_push.py`
- **Redis Queue**: Job queuing and execution

## Data Flow

1. **Request**: Client sends HTTP request to FastAPI endpoint
2. **Cache Check**: Endpoint checks Redis for cached data
3. **Cache Hit**: Return cached data if available
4. **Cache Miss**: Call service layer for fresh data
5. **Scraping**: Service makes HTTP request to vlr.gg
6. **Parsing**: Extract and transform data from HTML
7. **Response**: Return data to client
8. **Background Cache**: Cron jobs periodically refresh cache

## Design Patterns

### Repository Pattern
Services act as repositories, abstracting data access logic.

### Dependency Injection
FastAPI's dependency system for clean component coupling.

### Async/Await
Asynchronous operations for concurrent requests and I/O.

### Factory Pattern
Dynamic model creation and configuration.

## Technology Stack

- **Framework**: FastAPI (ASGI)
- **HTTP Client**: httpx (async HTTP)
- **HTML Parser**: BeautifulSoup with lxml
- **Cache**: Redis
- **Job Queue**: arq (Redis-based)
- **Validation**: Pydantic
- **Serialization**: JSON
- **Testing**: pytest with asyncio
- **Linting**: ruff
- **Type Checking**: ty

## Performance Considerations

- **Async Operations**: Non-blocking I/O for concurrent requests
- **Caching**: Redis reduces load on vlr.gg and improves response times
- **Background Updates**: Cron jobs prevent cache stampedes
- **Connection Pooling**: httpx client reuse for efficient HTTP requests

## Scalability

- **Horizontal Scaling**: Stateless design allows multiple instances
- **Redis Clustering**: Cache can be scaled independently
- **Job Distribution**: arq supports multiple workers
- **Rate Limiting**: Respect vlr.gg limits to avoid bans

## Security

- **Input Validation**: Pydantic models prevent malformed data
- **HTTPS**: Secure communication with vlr.gg
- **API Keys**: Optional authentication for sensitive endpoints
- **Error Handling**: Generic error responses prevent information leakage

## Monitoring

- **Logging**: Structured logging with context
- **Metrics**: FastAPI middleware for request metrics
- **Health Checks**: `/health` endpoint for monitoring
- **Error Tracking**: Sentry integration for error reporting

## Deployment

Production runs as a systemd user service (`deploy/systemd/vlrgg-scraper.service`),
installed by `scripts/install-systemd-user.sh` and updated by `scripts/deploy.sh`. A
`Dockerfile` is also available.

- The service runs Gunicorn with the `uvicorn_worker.UvicornWorker` adapter on
  `gunicorn.sock`. It uses one web worker because each web process also starts arq
  when caching is enabled.
- Gunicorn replaces a worker that stops heartbeating for 60 seconds; this is not a
  maximum duration for an async request. Graceful shutdown has a 60-second Gunicorn
  deadline inside systemd's 90-second stop deadline.
- The app loads configuration from `.env`. The installer syncs locked production
  dependencies (`--no-dev`), installs and reloads the unit, enables it, and restarts
  it. Startup uses `.venv/bin/gunicorn` directly, without syncing dependencies.
  Deploys reinstall the unit so server-command changes take effect.
- The Unix socket keeps its `0666` mode for Nginx; restrict access with the
  containing directory's permissions or ACLs. Other files are created owner-only
  (`UMask=0077`). Proxy-header trust is unchanged, so verify Nginx can connect before
  changing socket permissions or trusting forwarded headers from all peers.
- The embedded arq supervisor reconnects after Redis restarts without recycling the
  web worker. Worker sizing and supervising arq separately remain open decisions.
- GitHub Actions runs the offline tests (`ci.yml`), scheduled live parser checks
  (`parser-health.yml`), and builds the Docker image to
  `ghcr.io/akhilnarang/vlrgg-scraper` (`build-docker-image.yml`). The systemd deploy
  is run by hand.

## Development Workflow

1. **Local Development**: `uv run fastapi dev` with auto-reload
2. **Testing**: see [testing.md](testing.md)
3. **Linting**: `uv run ruff check` and `uv run ty check`
4. **Documentation**: Auto-generated OpenAPI docs
5. **Deployment**: `scripts/deploy.sh`, or the Docker image (see [Deployment](#deployment))
