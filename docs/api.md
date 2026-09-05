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
| GET | `/news/{id}` | Get an article with ordered content blocks |
| GET | `/player/{id}` | Get player statistics |
| GET | `/rankings` | Get current team rankings |
| GET | `/standings/{year}` | Get VCT standings for a year |
| GET | `/team/{id}` | Get team information |
| GET | `/search` | Search teams, players, and events |
| GET | `/version` | Get API version info |

## News article content

`GET /news/{id}` returns `blocks` in article document order. Clients should render
these blocks directly; no client-side HTML parsing is required. Text, links, media,
and nested content remain in their original positions.

| Block `type` | Content |
| --- | --- |
| `paragraph` | `runs` of text |
| `heading` | `runs` and heading `level`, from 1 to 6 |
| `blockquote` | Ordered `children` blocks |
| `list` | `children` containing `list_item` blocks; `ordered` and `start` specify numbering |
| `list_item` | Ordered `children`, including paragraphs and nested lists |
| `image` | Absolute `url` and `alt` text |
| `video` | Absolute `url`, including iframe embeds and native video sources |
| `caption` | `runs` from a figure caption or italic photo caption |

Each text run has `text`, an optional absolute link `url`, and `bold` and `italic`
flags. Concatenate runs exactly, retaining their spaces and newlines. A line break
is represented by `\n` within a run. Formatting and links can overlap.

For example, an introduction followed by a video and an interview question has
this block sequence. Unused fields are omitted here for readability; responses
include their defaults.

```json
[
  {"type": "paragraph", "runs": [{"text": "Interview introduction.", "italic": true}]},
  {"type": "video", "url": "https://www.youtube.com/embed/example"},
  {"type": "paragraph", "runs": [{"text": "How did the match feel?", "bold": true}]},
  {"type": "blockquote", "children": [
    {"type": "paragraph", "runs": [{"text": "We felt prepared."}]}
  ]}
]
```

The existing `content`, `links`, `images`, and `videos` fields are generated from
the same blocks. `content` uses `{{link_N}}`, `{image_N}`, and `{video_N}` as
zero-based references to the corresponding arrays. These references now occur
at their document positions, and heading text is included. Use `blocks` to retain
heading levels, emphasis, quotes, and list structure.

Article detail responses are scraped on request, unlike the cached news list.
Clients with locally cached article bodies should fetch an article again when
its cached response has no blocks.

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
