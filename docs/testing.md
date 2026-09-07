# Testing

Tests protect public behavior at project-owned boundaries. Keep the suite small
enough that a failure identifies a broken contract instead of a private refactor.

The [testing rules in AGENTS.md](../AGENTS.md#testing) govern test changes and
reviews. This document covers scraper-specific contracts and validation commands.

## Layout

- `tests/test_<service>.py` covers the public service or API boundary.
- `tests/fixtures/` contains minimal VLR HTML used by offline service tests.
- `tests/golden/` snapshots a small set of completed, stable VLR entities.
- `tests/live/` checks changing VLR pages through tolerant public contracts.

Service tests use the shared `http_response` and `http_get` fixtures from
`tests/conftest.py`:

```python
async def test_news_article_preserves_links(http_response):
    response = http_response(url, fixture.read_bytes())

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await news.news_by_id("562934")

    assert result.title == "EDward Gaming bids farewell to head coach Muggle"
```

For paginated services, pass URL-to-content routes to `http_get`. The hot path
must assert ordering and uniqueness. The failure path must prove that a later
HTTP error does not return or cache partial data.

Offline fixtures cover deterministic response values. Live checks cover VLR
markup drift using meaningful values, such as populated names and match scores;
schema-valid empty results can still indicate a broken scraper. Do not add live
variations that merely repeat the same contract.

## Commands

```bash
uv run pytest -m "not live_golden and not live_health"
uv run pytest -m live_health tests/live
uv run pytest -m live_golden tests/golden
uv run pytest tests/test_news.py
```

Run Ruff before committing:

```bash
uv run ruff check tests
uv run ruff format --check tests
```
