# Repository guidance

## Code

- Build the smallest change that works. Handle failures that have actually
  happened; add retries, locks, state machines, or concurrency limits only for
  an observed problem.
- Validate request input in Pydantic schemas (`Field` constraints, validators).
  Services receive already-valid data and only normalize it.
- Raise the HTTP errors in `app/exceptions.py`, adding a new one there when a
  status is missing, rather than raising `HTTPException` directly.
- Give repeated string literals (statuses, entity types) a constant or `StrEnum`
  in `app/constants.py`; put generic helpers in `app/utils.py`. Use `StrEnum`,
  not `(str, Enum)`: its members format as their value (`f"{MatchStatus.LIVE}"`
  is `live`), while a `(str, Enum)` member formats as `MatchStatus.LIVE`.
- Keep modules to one responsibility: database engine setup in `app/db/`,
  provider clients in `app/services/`, cron handlers in `app/cron/`.
- Transactions belong to the caller: the request's session dependency, or one
  session per match in a cron. Stores never commit. Commit before calling APNs,
  FCM, or VLR so the SQLite write lock is never held across network I/O.
- Log through a module logger (`logger = logging.getLogger(__name__)`). Fix lint
  findings in touched code instead of adding `# noqa`.
- Inline single-use variables.
- Write Python scripts as `.py` files run with `uv run`, not shell wrappers
  around Python.
- Docstrings use reST fields (`:param:`, `:return:`, `:raises:`). FastAPI route
  handlers get a one-line docstring only, because it becomes the Swagger
  description.

## Pull requests

- Keep a PR as a single commit, amended as it changes.
- Add an AI attribution line to a PR body only when that tool wrote the change
  without human intervention and review.

## Testing

- Strongly avoid creating new tests. Extend an existing focused test when it can
  cover the changed behavior; add a case only for an otherwise uncovered contract.
- Require behavioral contract coverage: assert public response values, observable
  side effects, and user-visible HTTP behavior. Each case must identify the
  regression it protects against.
- Cover the hot path and, at most, one meaningful failure case per contract.
  Parameterized variations count as cases; retain them only for distinct behavior.
- Do not test implementation details, private structure, or facts already checked
  by the typechecker. Public rich-text fields, media URLs, and playback requirements
  are contracts even when they describe formatting.
- Test adapters at project-owned boundaries. Mock external dependencies or service
  boundaries, and verify meaningful data crosses them. A canned fake answer alone
  does not prove the integration works.
- Keep UI tests limited to critical user-visible behavior, such as playable media
  and rejection of unsafe identifiers. Avoid exact markup or CSS assertions.
- Prefer product behavior over isolated tests of test helpers. Retain operational
  contracts such as the CI guard that rejects an all-skipped live run.
- Request changes for excessive, redundant, or non-contract tests. When pruning,
  preserve distinct behavior rather than optimizing test counts or coverage scores.

Before adding, modifying, removing, or reviewing tests, read
[docs/testing.md](docs/testing.md) for scraper-specific contracts and validation
commands.

When reporting before/after performance, use equivalent test selections and
dependencies, repeated fresh processes, and explicitly state which caches were
disabled. Measure coverage separately from timing. Report line and branch deltas;
execution coverage does not establish assertion strength, and small timing
differences within run-to-run variation do not establish a speedup or slowdown.
