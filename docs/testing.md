# Testing

Tests protect public behavior at project-owned boundaries. Keep the suite small
enough that a failure identifies a broken contract instead of a private refactor.

## Rules

- Strongly avoid new tests. Extend the existing focused test for that behavior.
- Cover the hot path and, at most, one meaningful failure path.
- Assert public response values and user-visible HTTP behavior.
- Do not assert private helpers, route trees, internal types, or schema defaults.
- Do not duplicate the type checker.
- Test adapters where project code calls them, such as cache, Firebase, or Sentry.
- Reject redundant variations and tests of test infrastructure.

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
