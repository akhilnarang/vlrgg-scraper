# Standings API

## Overview
The Standings API provides VCT (Valorant Champions Tour) standings data for a given year, scraped from vlr.gg. It returns standings grouped by circuit (e.g., North America, EMEA, Brazil, etc.), with teams ordered by total points and assigned sequential ranks.

## Endpoint
- **URL**: `/api/v1/standings/{year}`
- **Method**: GET
- **Parameters**:
  - `year` (path, integer): The VCT year (2021 to current year)
- **Response**: JSON object with year and list of circuits, each containing teams with details.

## Response Schema
```json
{
  "year": 2021,
  "circuits": [
    {
      "region": "North America Circuit",
      "teams": [
        {
          "name": "Sentinels",
          "id": 2,
          "logo": "https://owcdn.net/img/62875027c8e06.png",
          "rank": 1,
          "points": 775,
          "country": "United States"
        },
        // ... more teams
      ]
    },
    // ... more circuits
  ]
}
```

## Implementation Details
- **Scraping**: Fetches HTML from `https://www.vlr.gg/vct-{year}/standings` using httpx and parses with BeautifulSoup.
- **Parsing**: Extracts circuits from `div.eg-standing-group`, then teams from tables within, assigning ranks sequentially.
- **Caching**: Endpoint checks Redis cache first (`standings_{year}` key). If not present, scrapes fresh data and returns it. Cache is populated daily at midnight by a cron job for the current year only.
- **Validation**: Year must be between 2021 and current year.
- **Error Handling**: Returns HTTP 422 for invalid years, 500 for scraping failures.

## Files Modified/Created
- `app/constants.py`: Added `STANDINGS_URL`
- `app/schemas/standings.py`: Defined `TeamStanding`, `CircuitStanding`, `Standings` models
- `app/services/standings.py`: Implemented `standings_list()` for scraping and parsing
- `app/api/v1/endpoints/standings.py`: Created endpoint with cache-first logic
- `app/api/v1/api.py`: Registered standings router
- `app/schemas/__init__.py`: Imported new schemas
- `app/cron.py`: Added `standings_cron()` to cache current year's data daily at 00:00

## Design Decisions
- **Cache Strategy**: Read-only in endpoint (no cache writes on requests) to avoid slow first requests; background cron ensures cache freshness.
- **Year Range**: Dynamic upper bound (current year) to support future years without code changes.
- **Parsing**: Handles all circuits and teams, including collapsed ones in HTML.
- **Cron Frequency**: Daily at midnight to balance freshness and resource usage, focusing only on current year.