# Testing Structure

This document describes the testing structure and conventions used in this project.

## Overview

The project uses pytest for testing, with async support via pytest-asyncio. Tests are located in the `tests/` directory, with fixtures in `tests/fixtures/`.

## Test Structure

### File Naming
- Test files are named `test_<module>.py` (e.g., `test_news.py`, `test_events.py`, `test_matches.py`)
- Fixtures are named `<data>.html` (e.g., `news.html`, `events.html`, `match_12345.html`)

### Test Functions
- Test functions are named `test_<functionality>()`
- Async tests use `@pytest.mark.asyncio` decorator
- Tests follow the pattern: Arrange, Act, Assert

### Common Patterns

#### HTTP Mocking
```python
mock_response = AsyncMock()
mock_response.status_code = 200
mock_response.content = html_content.encode("utf-8")

with patch("httpx.AsyncClient.get", return_value=mock_response):
    result = await service.function()
```

#### Redis Mocking
```python
mock_redis = AsyncMock()
```

#### Multiple Responses
```python
with patch("httpx.AsyncClient.get", side_effect=[response1, response2]):
    result = await service.function()
```

## Service Test Examples

### News Tests
- `test_news_list()`: Tests fetching and parsing news list from `/news`
- `test_news_by_id()`: Tests fetching and parsing individual news article from `/{id}`
- Uses fixtures `news.html` and `news_562952.html`
- Asserts correct parsing of title, description/content, URL, author, tags

### Events Tests
- `test_get_events()`: Tests fetching events list
- `test_get_event_by_id()`: Tests fetching detailed event info
- Uses fixtures `events.html` and `event_2283.html`
- Asserts correct parsing of event properties

### Matches Tests
- `test_match_list()`: Tests fetching matches list
- `test_match_by_id()`: Tests fetching detailed match info
- Uses fixture `match_12345.html`
- Asserts correct parsing of teams, event, videos, etc.

## Running Tests

### All Tests
```bash
uv run pytest
```

### Specific Test File
```bash
uv run pytest tests/test_news.py
```

### Specific Test Function
```bash
uv run pytest tests/test_news.py::test_news_list
```

### With Verbose Output
```bash
uv run pytest -v
```

## Configuration

- `pyproject.toml` contains pytest configuration
- `pythonpath = "."` ensures correct module imports
- Async tests use `asyncio_default_fixture_loop_scope=function`

## Fixtures

Fixtures are HTML files that mimic the structure of VLR.gg pages:
- Stored in `tests/fixtures/`
- Used to mock HTTP responses
- Contain minimal but valid HTML structure for parsing

## Mocking Strategy

- HTTP requests are mocked using `unittest.mock.patch`
- Redis client is mocked as `AsyncMock()`
- External dependencies are isolated to test parsing logic

## Assertions

- Check return types and structures
- Verify key data fields are present and correct
- Ensure no exceptions are raised during parsing
- Test edge cases where possible

## Adding New Tests

1. Create test file `tests/test_<service>.py`
2. Add fixtures in `tests/fixtures/` if needed
3. Write test functions following the patterns above
4. Run tests to ensure they pass
5. Update this documentation if new patterns are introduced