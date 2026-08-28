# Web/API dependency upgrade audit

## Scope and 72-hour rule

This audit compares the committed `uv.lock` at `HEAD` with the working-tree lock for the assigned web, validation, CLI, and ASGI packages.

The working lock records `exclude-newer = "2026-08-25T07:55:48.804499828Z"` and `exclude-newer-span = "P3D"`. A selected artifact is therefore eligible only when its official PyPI upload is no later than that cutoff. Every changed package below passes. The newest one in this slice is `fastar 0.12.0`, uploaded `2026-08-20T09:07:32.822Z`, about 190 hours and 48 minutes before resolution: nearly five days beyond the required 72-hour waiting period.

Upload timestamps below are the earliest selected-artifact timestamps recorded in the working `uv.lock`; each package name links to the corresponding official PyPI release JSON.

## Versions, age, and decisions

| Package | `HEAD` | Working tree | Earliest upload (UTC) | 72-hour result | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| [`annotated-doc`](https://pypi.org/pypi/annotated-doc/0.0.5/json) | 0.0.4 | 0.0.5 | `2026-07-28T13:50:57.239Z` | Eligible | **Keep.** Only relevant breaking change is removal of Python 3.8 support; this project requires Python 3.14. |
| [`annotated-types`](https://pypi.org/pypi/annotated-types/0.8.0/json) | 0.7.0 | 0.8.0 | `2026-07-23T20:16:12.938Z` | Eligible | **Keep.** Note the public `DocInfo` to `Doc` rename; the repository has no direct import. |
| [`anyio`](https://pypi.org/pypi/anyio/4.14.2/json) | 4.12.1 | 4.14.2 | `2026-07-12T20:29:05.763Z` | Eligible | **Keep.** Async behavior changes are compatible with the app's use through Starlette/FastAPI, and the range contains important deadlock and cancellation fixes. |
| [`click`](https://pypi.org/pypi/click/8.4.2/json) | 8.3.1 | 8.4.2 | `2026-06-24T17:45:13.73Z` | Eligible | **Keep.** The repository does not import Click; changes affect transitive CLI behavior and typing. |
| [`fastapi`](https://pypi.org/pypi/fastapi/0.136.3/json) | 0.136.3 | 0.136.3 | `2026-05-23T18:53:15.192Z` | Eligible; unchanged | **Keep at 0.136.3.** Preserve the new `<0.137` constraint until the router-tree change is tested separately. |
| [`fastapi-cli`](https://pypi.org/pypi/fastapi-cli/0.0.32/json) | 0.0.24 | 0.0.32 | `2026-07-16T12:16:57.297Z` | Eligible | **Keep.** Mostly startup/logging presentation changes plus `FASTAPI_ENV`; no application runtime API change. |
| [`fastapi-cloud-cli`](https://pypi.org/pypi/fastapi-cloud-cli/0.23.0/json) | 0.15.0 | 0.23.0 | `2026-07-28T14:03:33.463Z` | Eligible | **Keep.** The changes expand cloud-management commands and improve deploy/error handling; this is deployment CLI code, not request handling. |
| [`fastar`](https://pypi.org/pypi/fastar/0.12.0/json) | 0.8.0 | 0.12.0 | `2026-08-20T09:07:32.822Z` | Eligible by about 118h beyond minimum | **Keep.** Includes a truncated-zstd-archive fix and packaging/runtime maintenance. |
| [`markdown-it-py`](https://pypi.org/pypi/markdown-it-py/4.2.0/json) | 4.0.0 | 4.2.0 | `2026-05-07T12:08:27.182Z` | Eligible | **Keep.** New presets and fence customization plus a quadratic-complexity fix; no declared breaking change. |
| [`pydantic`](https://pypi.org/pypi/pydantic/2.13.4/json) | 2.12.5 | 2.13.4 | `2026-05-06T13:43:02.641Z` | Eligible | **Keep with API/golden coverage.** There are observable exception, serialization, and field-tracking changes described below. |
| [`pydantic-core`](https://pypi.org/pypi/pydantic-core/2.46.4/json) | 2.41.5 | 2.46.4 | `2026-05-06T13:36:53.615Z` | Eligible | **Keep with Pydantic.** It is the core paired with Pydantic 2.13.4 and includes validation/serialization fixes and performance work. |
| [`pydantic-settings`](https://pypi.org/pypi/pydantic-settings/2.15.0/json) | 2.14.2 | 2.15.0 | `2026-08-07T09:24:55.839Z` | Eligible | **Keep.** Case sensitivity now consistently applies to init/config-file sources; current settings are loaded from environment/`.env` without init kwargs. |
| [`python-dotenv`](https://pypi.org/pypi/python-dotenv/1.2.3/json) | 1.2.2 | 1.2.3 | `2026-08-16T16:54:52.473Z` | Eligible | **Keep.** Fix-only release for BOMs, backslashes, empty files, and missing CLI commands. |
| [`python-multipart`](https://pypi.org/pypi/python-multipart/0.0.32/json) | 0.0.31 | 0.0.32 | `2026-06-04T16:18:57.319Z` | Eligible | **Keep.** Replaces inefficient per-byte partial-boundary scanning; no public API change. |
| [`rich-toolkit`](https://pypi.org/pypi/rich-toolkit/0.20.3/json) | 0.19.7 | 0.20.3 | `2026-07-13T14:38:05.687Z` | Eligible | **Keep.** Non-interactive/CI progress logs are now preserved by default; relevant only to CLI output. |
| [`starlette`](https://pypi.org/pypi/starlette/1.6.0/json) | 1.3.1 | 1.6.0 | `2026-08-08T18:27:56.196Z` | Eligible | **Keep.** GZip behavior changes affect this app but fix streaming, partial-response, and event-loop issues. |
| [`typer`](https://pypi.org/pypi/typer/0.27.1/json) | 0.24.1 | 0.27.1 | `2026-08-03T14:41:03.438Z` | Eligible | **Keep.** It has an important Click-vendoring break, but the repository does not import Typer or use Click plug-ins; it is reached through CLI extras. |
| [`typing-extensions`](https://pypi.org/pypi/typing-extensions/4.16.0/json) | 4.15.0 | 4.16.0 | `2026-07-02T08:40:04.659Z` | Eligible | **Keep.** Runtime typing fixes and Python 3.15 support; only sentinel users need to note naming/repr changes. |
| [`typing-inspection`](https://pypi.org/pypi/typing-inspection/0.4.4/json) | 0.4.2 | 0.4.4 | `2026-08-12T12:37:24.648Z` | Eligible | **Keep.** Drops Python 3.9 and adds deprecated-alias metadata; no direct use in the repository. |
| [`uvicorn`](https://pypi.org/pypi/uvicorn/0.52.4/json) | 0.50.2 | 0.52.4 | `2026-08-19T06:27:40.36Z` | Eligible | **Keep.** WebSocket close/backpressure fixes and rolling `SIGHUP` worker replacement are operational improvements; experimental `zttp` stays opt-in. |
| [`watchfiles`](https://pypi.org/pypi/watchfiles/1.2.0/json) | 1.1.1 | 1.2.0 | `2026-05-18T04:30:06.891Z` | Eligible | **Keep.** Drops Python 3.9 and improves error handling/wheel coverage; production request handling is unaffected. |
| [`websockets`](https://pypi.org/pypi/websockets/17.0.1/json) | 16.0 | 17.0.1 | `2026-07-31T11:30:22.436Z` | Eligible | **Keep with a WebSocket smoke test if WebSockets are deployed.** Major-version API breaks do not appear in repository code, and 17.0.1 fixes uvloop compatibility. |

## Important compatibility findings

### Framework, ASGI, and transport

- **FastAPI remains 0.136.3.** The new `<0.137` cap is justified by FastAPI's official [0.137.0 release note](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/release-notes.md#01370): `router.routes` is no longer guaranteed to be a flat list of `APIRoute` objects and can contain intermediate router-tree objects. Code that walks or mutates that list must migrate to the newer traversal API. No repository match directly accesses `router.routes`, but this is still a documented breaking framework-internals change and should be upgraded independently from the bulk dependency refresh.

- **Starlette 1.3.1 to 1.6.0.** The official [release notes](https://github.com/Kludex/starlette/blob/1.6.0/docs/release-notes.md) add an application/route body-size limit and change `GZipMiddleware`: large compression work can move to a thread, resources are allocated lazily, streamed chunks are flushed, partial responses are not compressed, and more content types are excluded by default. This repository installs `GZipMiddleware(minimum_size=500)`, so compressed headers and chunk timing can change, but these are correctness and event-loop responsiveness improvements. Keep and rely on API/golden tests for response compatibility.

- **Uvicorn 0.50.2 to 0.52.4.** The official [release notes](https://github.com/Kludex/uvicorn/blob/0.52.4/docs/release-notes.md) add rolling worker replacement on `SIGHUP`, fix several WebSocket close-handshake/backpressure/header issues, and add an experimental Zig-backed `zttp` HTTP parser. `zttp` is opt-in through `--http zttp`; the default protocol selection does not expose production traffic to it. Keep.

- **websockets 16.0 to 17.0.1.** The official [17.0 changelog](https://github.com/python-websockets/websockets/blob/17.0.1/docs/project/changelog.rst#170) documents the major breaks: Python 3.11 minimum, removal of module aliases deprecated since 9.0, several boolean arguments becoming keyword-only, ISO-8859-1 handshake-header encoding, `process_request` seeing non-GET/HTTP/1.0 requests, and `socket=` becoming `sock=` in the threading implementation. The [17.0.1 patch](https://github.com/python-websockets/websockets/blob/17.0.1/docs/project/changelog.rst#1701) restores `serve_forever()` compatibility with uvloop and fixes Trio backpressure. The repository has no direct `websockets` import and runs Python 3.14; Uvicorn 0.52 explicitly supports this line. Keep, with a WebSocket handshake/close smoke test if that protocol is exposed in deployment.

### Validation and settings

- **Pydantic/Pydantic Core 2.12.5/2.41.5 to 2.13.4/2.46.4.** The official [Pydantic 2.13 releases](https://github.com/pydantic/pydantic/releases/tag/v2.13.4) include substantial validation and serialization work. Important observable changes are: `PydanticUserError` now derives from `RuntimeError` instead of `TypeError`; serialization no longer tries every union member after a discriminator-selected variant fails; extra fields assigned after initialization are tracked in `model_fields_set`; fixed-length tuples with too few items produce a serialization warning; and `model_validate_json()` now supplies `ValidationInfo.data` and `field_name` correctly. It also adds `polymorphic_serialization`, fixes `serialize_as_any` regressions, and fixes several JSON/model serialization edge cases. This app uses Pydantic models throughout its HTTP responses and cached JSON paths, so API/golden coverage is the right gate. Keep the paired versions rather than mixing core versions.

- **pydantic-settings 2.14.2 to 2.15.0.** The official [2.15.0 notes](https://github.com/pydantic/pydantic-settings/releases/tag/v2.15.0) call out behavior changes: `case_sensitive` now applies to constructor kwargs and JSON/TOML/YAML sources, unresolved forward references warn, and non-JSON environment values for strict fields raise `ValidationError`. This repository sets only `env_file=".env"`, does not set `case_sensitive`, and creates `Settings()` without kwargs, so the newly affected paths are not used. The release also fixes dotenv-prefix collisions, nested optional model matching, discriminated-union partial updates, and secret-file handling. Keep.

- **annotated-types 0.7.0 to 0.8.0.** The official [0.8.0 release](https://github.com/annotated-types/annotated-types/releases/tag/v0.8.0) renames `DocInfo` to `Doc` and drops Python 3.8/3.9 while adding 3.13/3.14 support. This can break direct `DocInfo` imports, but the repository has none. Keep as a Pydantic transitive dependency.

- **annotated-doc 0.0.4 to 0.0.5.** The official [release notes](https://github.com/fastapi/annotated-doc/blob/main/release-notes.md#005-2026-07-28) list only Python 3.8 support removal as a user-facing break. Keep on Python 3.14.

### Async and multipart processing

- **AnyIO 4.12.1 to 4.14.2.** The official [version history](https://github.com/agronholm/anyio/blob/4.14.2/docs/versionhistory.rst) drops Python 3.9, makes `TaskGroup.start_soon()` return a `TaskHandle`, adds `TaskGroup.create_task()`/`cancel()`, narrows callable annotations, adds `__slots__` to synchronization classes, and makes byte receive streams reject non-positive sizes. The range also fixes asyncio lock/semaphore races, cancellation CPU spin, process-pipe deadlocks, capacity-limiter over-granting, TLS IDNA matching, and subprocess argument forwarding. The app does not directly import AnyIO; keep through Starlette/FastAPI.

- **python-multipart 0.0.31 to 0.0.32.** The official [release](https://github.com/Kludex/python-multipart/releases/tag/0.0.32) replaces its per-byte partial-boundary scan with an `rfind` lookbehind. This is a parser performance/correctness change without a public API break. Keep.

### CLI and presentation dependencies

- **Typer 0.24.1 to 0.27.1.** The official [release notes](https://github.com/fastapi/typer/blob/master/docs/release-notes.md#0260-2026-05-26) identify the main breaking change in 0.26.0: Typer vendors Click and no longer supports extracting the underlying Click app, Click-specific plug-ins, or Click-specific custom parameter types. Version 0.27.0 also changes metavar rendering. The repository has no direct Typer or Click integration; these packages arrive with FastAPI's CLI extras. Keep, but downstream scripts that customize the FastAPI CLI with Click internals would need migration.

- **Click 8.3.1 to 8.4.2.** The official [8.4.2 changelog](https://github.com/pallets/click/blob/8.4.2/CHANGES.md) includes type-system/API tightening (`Parameter` becomes abstract and `ParamType` becomes generic), default-map splitting for tuple values, prompt/readline behavior changes, a new `CliRunner` file-descriptor capture mode, and fixes for shell completion, pagers, optional subcommand usage, and version lookup. It is only a transitive CLI dependency here. Keep.

- **FastAPI CLI 0.0.24 to 0.0.32.** The official [release notes](https://github.com/fastapi/fastapi-cli/blob/main/release-notes.md#0032-2026-07-16) add `FASTAPI_ENV` for `fastapi dev`, add `--verbose`, reduce default startup output, avoid fancy logs for non-TTY output, and fix duplicate Uvicorn logs. These intentionally change console output, not served API behavior. Keep.

- **FastAPI Cloud CLI 0.15.0 to 0.23.0.** The official [release notes](https://github.com/fastapilabs/fastapi-cloud-cli/blob/main/release-notes.md#0230-2026-07-28) add app/team/deployment/token/environment/CI management commands, broad JSON output, deployment-log improvements, input validation, and archive-size handling. No breaking change is declared in the interval. Keep.

- **rich-toolkit 0.19.7 to 0.20.3.** The official [changelog](https://github.com/patrick91/rich-toolkit/blob/main/CHANGELOG.md#0203---2026-07-13) adds JSON/JSONL output and, in CI/non-interactive output, preserves progress logs automatically. It also keeps updated progress titles visible. This can alter captured deployment-CLI logs by design; it does not affect HTTP responses. Keep.

### Lower-risk support libraries

- **fastar 0.8.0 to 0.12.0.** Official releases [0.9.0 through 0.12.0](https://github.com/DoctorJohn/fastar/releases/tag/v0.12.0) add sparse-file handling, multithreaded tests, platform wheels, and PyO3 updates. The intervening [0.10.1 patch](https://github.com/DoctorJohn/fastar/releases/tag/v0.10.1) fixes truncated zstd archives. Keep.

- **markdown-it-py 4.0.0 to 4.2.0.** The official [changelog](https://github.com/executablebooks/markdown-it-py/blob/v4.2.0/CHANGELOG.md) adds a GFM-like preset, plugin-defined inline terminators, stdin CLI input, configurable fence rules, and a quadratic-complexity fix. No breaking change is declared. Keep.

- **python-dotenv 1.2.2 to 1.2.3.** The official [changelog](https://github.com/theskumar/python-dotenv/blob/main/CHANGELOG.md#123---2026-08-16) strips UTF-8 BOMs, correctly escapes backslashes in `set_key`, avoids re-reading empty files, and handles a missing `dotenv run` command cleanly. Keep.

- **typing-extensions 4.15.0 to 4.16.0.** The official [changelog](https://github.com/python/typing_extensions/blob/main/CHANGELOG.md#release-4160-july-2-2025) adds Python 3.15 support and fixes several protocol, typed-dictionary, deprecation, and runtime-inspection cases. `Sentinel` is soft-deprecated in favor of `sentinel`, sentinel repr changes from `<X>` to `X`, and some sentinel construction/mutation forms are deprecated. No repository code uses these APIs. Keep. The heading's year says 2025, but the release-candidate dates and official PyPI upload are in 2026; the PyPI timestamp above governs age eligibility.

- **typing-inspection 0.4.2 to 0.4.4.** The official [0.4.3 release](https://github.com/pydantic/typing-inspection/releases/tag/v0.4.3) drops Python 3.9 and avoids module `getattr()` calls; the official [0.4.3-to-0.4.4 comparison](https://github.com/pydantic/typing-inspection/compare/v0.4.3...v0.4.4) adds `DEPRECATED_ALIASES_IDS` and CI fixes. Keep.

- **watchfiles 1.1.1 to 1.2.0.** The official [release](https://github.com/samuelcolvin/watchfiles/releases/tag/v1.2.0) drops Python 3.9, raises the Rust toolchain floor, adds Python 3.15/riscv64 coverage, and improves type safety/error handling. This is used for development reload behavior rather than normal production requests. Keep.

## Final recommendation

Keep every changed working-tree version in this assigned set. All selected releases pass the permanent 72-hour policy by a wide margin, and none needs an age-based rollback.

Keep FastAPI itself at `0.136.3` with `<0.137`; treat the router-tree migration as a separate upgrade. Before deployment, the compatibility-sensitive checks are the API/golden suite for Pydantic/Starlette behavior, normal environment-based settings startup, and a WebSocket handshake/close smoke test only if the service exposes WebSockets. CLI output snapshots, if any, may need updates because FastAPI CLI, Typer, Click, and rich-toolkit intentionally changed formatting and non-TTY logging.
