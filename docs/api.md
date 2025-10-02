# API Documentation

This document provides general information about the API. For detailed endpoint specifications, see the interactive API documentation at `/docs` (Swagger UI) or `/redoc` (ReDoc).

## Base URL
```
http://localhost:8000/api/v1/
```

## Authentication
Most endpoints are public. Some internal endpoints may require `X-API-Key` header.

## Available Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/events` | List events with optional status filtering |
| GET | `/events/{id}` | Get detailed event information |
| GET | `/matches` | List matches with filtering options |
| GET | `/matches/{id}` | Get detailed match information |
| GET | `/news` | Get latest news articles |
| GET | `/player/{id}` | Get player statistics |
| GET | `/rankings` | Get current team rankings |
| GET | `/standings/{year}` | Get VCT standings for a year |
| GET | `/team/{id}` | Get team information |
| GET | `/search` | Search teams, players, and events |
| GET | `/version` | Get API version info |

## Interactive Documentation

- **Swagger UI**: Visit `http://localhost:8000/docs` for interactive API testing
- **ReDoc**: Visit `http://localhost:8000/redoc` for alternative documentation view
- **OpenAPI JSON**: `http://localhost:8000/openapi.json` for programmatic access

## Error Responses

All endpoints return standard HTTP status codes:

- `200`: Success
- `400`: Bad Request
- `404`: Not Found
- `422`: Validation Error (Pydantic validation errors)
- `500`: Internal Server Error

Error response format:
```json
{
  "detail": "Error message"
}
```

## Rate Limiting
No explicit rate limiting implemented. Please respect vlr.gg's servers and avoid excessive requests.

## Caching
Endpoints use Redis caching with TTL. Cache keys are set by background cron jobs. See [Caching Documentation](caching.md) for details.