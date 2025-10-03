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
- **Connections**: Database, Redis, and semaphore for rate limiting
- **Utilities**: Helper functions for common operations

### Cron (`app/cron/`)
- **Background Jobs**: Periodic data updates using arq
- **Scheduling**: Cron-like job scheduling
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
- **Type Checking**: mypy

## Performance Considerations

- **Async Operations**: Non-blocking I/O for concurrent requests
- **Caching**: Redis reduces load on vlr.gg and improves response times
- **Background Updates**: Cron jobs prevent cache stampedes
- **Connection Pooling**: httpx client reuse for efficient HTTP requests
- **Rate Limiting**: Redis-based distributed semaphore limits concurrent requests to vlr.gg across all processes

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

- **Containerization**: Docker for consistent environments
- **Orchestration**: Kubernetes for production scaling
- **CI/CD**: GitHub Actions for automated testing and deployment
- **Environment Config**: 12-factor app principles

## Development Workflow

1. **Local Development**: `uv run fastapi dev` with auto-reload
2. **Testing**: `uv run pytest` with coverage
3. **Linting**: `uv run ruff check` and `uv run mypy`
4. **Documentation**: Auto-generated OpenAPI docs
5. **Deployment**: Docker build and push to registry

