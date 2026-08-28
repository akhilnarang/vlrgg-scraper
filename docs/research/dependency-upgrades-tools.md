# Dependency upgrade audit: tooling and OpenAI packages

## Scope and release-age rule

This audit compares the committed `uv.lock` at `HEAD` with the current working-tree lock for the assigned tooling, OpenAI, and related transitive packages.

The working lock records `exclude-newer = "2026-08-25T07:55:48.804499828Z"` and `exclude-newer-span = "P3D"`. Its resolution time was therefore `2026-08-28T07:55:48.804499828Z`, and a selected artifact is eligible only if its earliest official PyPI upload was at or before the recorded cutoff. Every addition and upgrade below passes that 72-hour rule. `virtualenv 21.7.5` is the closest: it was 74 hours, 16 minutes, and 34 seconds old at resolution, about 2 hours and 16 minutes beyond the minimum.

The timestamps below are the earliest exact `upload_time_iso_8601` values among the files in each linked official PyPI release document. Removals introduce no release, so their age result is recorded as not applicable.

## Version and eligibility decisions

| Package | `HEAD` | Working tree | Earliest upload (UTC) | 72-hour result | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| [`detect-installer`](https://pypi.org/pypi/detect-installer/0.1.0/json) | absent | 0.1.0 | `2026-02-23T10:40:22.567074Z` | Eligible | Keep. It is a dependency-free helper newly required by `fastapi-cloud-cli`. |
| [`distlib`](https://pypi.org/pypi/distlib/0.4.3/json) | 0.4.0 | 0.4.3 | `2026-06-12T08:04:50.506662Z` | Eligible | Keep. The intervening releases contain important path and archive hardening. |
| [`filelock`](https://pypi.org/pypi/filelock/3.32.4/json) | 3.25.2 | 3.32.4 | `2026-08-23T17:37:53.913430Z` | Eligible | Keep. It is a transitive development dependency and includes lock correctness and security fixes. |
| [`gunicorn`](https://pypi.org/pypi/gunicorn/26.2.0/json) | 26.0.0 | 26.2.0 | `2026-08-24T15:05:57.670955Z` | Eligible | Keep. HTTP/2 and request-policy fixes are worthwhile; new cleartext HTTP/2 remains disabled by default. |
| [`identify`](https://pypi.org/pypi/identify/2.6.19/json) | 2.6.18 | 2.6.19 | `2026-04-17T18:39:49.221126Z` | Eligible | Keep. This is a small file-identification update used by `pre-commit`. |
| [`jiter`](https://pypi.org/pypi/jiter/0.16.0/json) | 0.15.0 | 0.16.0 | `2026-06-29T13:02:31.356972Z` | Eligible | Keep. `openai 3.3.1` requires this version range; the dropped 3.13t target does not affect the repository. |
| [`openai`](https://pypi.org/pypi/openai/3.3.1/json) | 3.2.0 | 3.3.1 | `2026-08-19T16:31:32.812757Z` | Eligible | Keep. It contains dependency/security maintenance and intentionally removes unused dependencies. |
| [`packaging`](https://pypi.org/pypi/packaging/26.3/json) | 26.0 | 26.3 | `2026-08-04T18:15:27.159957Z` | Eligible | Keep. The Python 3.8 support removal is harmless because the project requires Python 3.14. |
| [`platformdirs`](https://pypi.org/pypi/platformdirs/4.11.4/json) | 4.9.4 | 4.11.4 | `2026-08-24T14:53:48.406471Z` | Eligible | Keep. It adds Python 3.15 support and multiple platform path fixes. |
| [`Pygments`](https://pypi.org/pypi/pygments/2.21.0/json) | 2.20.0 | 2.21.0 | `2026-08-17T08:02:44.912148Z` | Eligible | Keep. Changes affect highlighting and formatter output used transitively by pytest and Rich. |
| [`python-discovery`](https://pypi.org/pypi/python-discovery/1.5.3/json) | 1.2.0 | 1.5.3 | `2026-08-24T14:48:45.305622Z` | Eligible | Keep. It improves uv, symlink, debug, free-threaded, and older-interpreter discovery. |
| [`rignore`](https://pypi.org/pypi/rignore/0.8.1/json) | 0.7.6 | 0.8.1 | `2026-08-04T22:22:48.897012Z` | Eligible | Keep. It includes Rust dependency security fixes and supports Python 3.15 wheels. |
| [`ruff`](https://pypi.org/pypi/ruff/0.16.4/json) | 0.16.3 | 0.16.4 | `2026-08-20T17:43:16.888576Z` | Eligible | Keep if the lint job passes. Rule and parser fixes can legitimately change diagnostics. |
| [`sentry-sdk`](https://pypi.org/pypi/sentry-sdk/2.68.1/json) | 2.64.0 | 2.68.1 | `2026-08-24T13:09:36.186944Z` | Eligible | Keep. Note the logging/metrics option deprecations, but the repository does not set those options. |
| [`ty`](https://pypi.org/pypi/ty/0.0.74/json) | 0.0.72 | 0.0.74 | `2026-08-22T15:05:05.015450Z` | Eligible | Keep if type checking passes. The added diagnostics are opt-in, but inference fixes can alter results. |
| [`virtualenv`](https://pypi.org/pypi/virtualenv/21.7.5/json) | 21.2.0 | 21.7.5 | `2026-08-25T05:39:14.229247Z` | Eligible by 2h 16m | Keep. It contains security and interpreter-discovery fixes needed by development tooling. |
| [`distro`](https://pypi.org/pypi/distro/1.9.0/json) | 1.9.0 | absent | `2023-12-24T09:54:30.421191Z` for the removed version | Not applicable | Keep removed. It was present only because `openai 3.2.0` required it. |
| [`tqdm`](https://pypi.org/pypi/tqdm/4.68.3/json) | 4.68.3 | absent | `2026-06-17T07:36:50.132386Z` for the removed version | Not applicable | Keep removed. It was present only because `openai 3.2.0` required it. |

## Important behavior and compatibility notes

### Runtime dependencies

- **Gunicorn 26.0.0 to 26.2.0:** The official [2026 release notes](https://gunicorn.org/2026-news/) describe reload glob support and logger reload fixes in 26.1, followed by opt-in cleartext HTTP/2 and streaming HTTP/2 responses in 26.2. HTTP/2 now shares HTTP/1 header policies, closing paths involving duplicate or malformed headers and incorrect forwarded scheme or peer handling. The releases also correct response framing for `HEAD`, 204, and 304 responses and prevent `sendfile` from bypassing framing. Cleartext HTTP/2 is off by default, so this is not an automatic protocol exposure. Keep.

- **OpenAI 3.2.0 to 3.3.1:** The official [3.3.0](https://github.com/openai/openai-python/releases/tag/v3.3.0) notes add named data-residency endpoints, raise vulnerable optional-network dependency floors, and remove unused dependencies. The [3.3.1](https://github.com/openai/openai-python/releases/tag/v3.3.1) patch updates dependencies with security fixes and moves platform detection to the standard library. There is no declared breaking API change in this interval. The old [3.2.0 dependency metadata](https://pypi.org/pypi/openai/3.2.0/json) requires `distro`, `tqdm`, and `jiter>=0.10.0`; the new [3.3.1 metadata](https://pypi.org/pypi/openai/3.3.1/json) removes `distro` and `tqdm` and raises Jiter to `>=0.16.0,<1`. Repository search finds no direct `distro` or `tqdm` import, so both removals are safe. Keep the upgrade and removals.

- **Sentry SDK 2.64.0 to 2.68.1:** The official [Sentry Python changelog](https://github.com/getsentry/sentry-python/blob/2.68.1/CHANGELOG.md) records tracing, batching, framework, messaging, and OpenAI-integration fixes across this range. The operationally important behavior change is in 2.68.0: `enable_logs` and `enable_metrics` became deprecated no-ops, and logging collection moved toward each logging integration's `capture_sentry_logs` option, which defaults to false. Version 2.68.1 restores compatibility for automatic log collection when `enable_logs=True`; `enable_metrics` remains a no-op, and both switches are scheduled for removal in the next major version. This repository's Sentry initialization does not set any of these options, so current behavior is unaffected. Keep, while treating future use of the deprecated flags as migration work.

### Transitive runtime and packaging libraries

- **detect-installer 0.1.0:** This is the initial release, so there is no old behavior to preserve. Its official [PyPI description](https://pypi.org/project/detect-installer/0.1.0/) documents a dependency-free detector for pip, uv projects, uv tools, pipx, Homebrew, Conda, and Mamba that returns the corresponding upgrade command. The official [`fastapi-cloud-cli 0.23.0` metadata](https://pypi.org/pypi/fastapi-cloud-cli/0.23.0/json) declares `detect-installer>=0.1.0`, explaining the new lock entry. Keep as required transitive state.

- **distlib 0.4.0 to 0.4.3:** The official [distlib changelog](https://github.com/pypa/distlib/blob/0.4.3/CHANGES.rst) records an entry-point path-traversal fix in 0.4.1 and broader archive, wheel, resource, symlink, decompression, and path-containment hardening in 0.4.2. Version 0.4.3 removes an overly restrictive resource escape check. These changes are security- and correctness-oriented for `virtualenv`; keep.

- **filelock 3.25.2 to 3.32.4:** The official [3.32.4 changelog](https://github.com/tox-dev/filelock/blob/3.32.4/docs/changelog.rst) spans significant hardening: symlink and FIFO defenses, stale-lock handling, cancellation and fork safety, transactional releases, Android support, and fixes to strict, lease, and read/write lock claims. Version 3.29.5 intentionally leaves Unix lock files in place after release, which is a behavior change for consumers that expected unlinking. `virtualenv` and `python-discovery` use file locking transitively, and no repository code relies on lock-file deletion. Keep.

- **Jiter 0.15.0 to 0.16.0:** The official [0.16.0 release](https://github.com/pydantic/jiter/releases/tag/v0.16.0) adds a Rust `serde::Deserializer`, updates PyO3, modernizes Emscripten CI, and drops the Python 3.13 free-threaded target. The project runs Python 3.14, and OpenAI now requires Jiter 0.16 or newer. Keep.

- **packaging 26.0 to 26.3:** The official [26.3 changelog](https://github.com/pypa/packaging/blob/26.3/CHANGELOG.rst) covers dependency groups, direct URLs, range-backed specifiers, tag and filename parsing, pylock work, Metadata 2.6, and the new public `VersionRange` API. It drops Python 3.8 and changes Linux tag preference to favor native `linux_*` tags over manylinux/musllinux tags. The repository requires Python 3.14 and does not directly use these APIs. Keep; packaging stays in the graph through pytest even though Gunicorn 26.2 no longer has it as a core dependency.

- **platformdirs 4.9.4 to 4.11.4:** The official [4.11.4 changelog](https://github.com/tox-dev/platformdirs/blob/4.11.4/docs/changelog.rst) adds public-share, templates, fonts, preference, and projects directories; Python 3.15 support; and fixes for Windows folder lookup, duplicate iterated directories, and separator-only XDG values. macOS `site_config_path` with multipath now returns the first path, which is a possible behavior change for direct callers. This project has no direct call and receives the package from `virtualenv`; keep.

- **Pygments 2.20.0 to 2.21.0:** The official [2.21.0 change log](https://github.com/pygments/pygments/blob/2.21.0/CHANGES) adds BitBake, Caddy, CEL, and PureScript lexers, updates many lexer definitions, supports Python 3.15, and fixes catastrophic backtracking in XML detection. `HtmlFormatter` now emits single and double quotes literally rather than as entities, and lexer token streams can differ. In this graph Pygments supports pytest/Rich output rather than application parsing. Keep.

- **python-discovery 1.2.0 to 1.5.3:** The official [1.5.3 changelog](https://github.com/tox-dev/python-discovery/blob/1.5.3/docs/changelog.rst) covers interpreter enumeration, uv-on-Windows support, GraalPy naming, debug and free-threaded builds, symlinks, Python 3.15, uv store selection, and restored discovery of Python 3.6/3.7. It also prefers a version-matched system executable for copied POSIX virtual environments. Version 1.5.1 drops the runtime dependency on `platformdirs`, but `platformdirs` correctly remains because `virtualenv` still requires it. Keep.

- **rignore 0.7.6 to 0.8.1:** The official [0.8.0 release](https://github.com/patrick91/rignore/releases/tag/0.8.0) updates PyO3 for RUSTSEC-2026-0176 and RUSTSEC-2026-0177 and drops PyPy 3.9/3.10 wheels. The [0.8.1 release](https://github.com/patrick91/rignore/releases/tag/0.8.1) adds Python 3.15, including free-threaded wheels. The repository targets CPython 3.14, so the removed PyPy targets are irrelevant. Keep as a `fastapi-cloud-cli` transitive dependency.

### Development tools

- **identify 2.6.18 to 2.6.19:** The official [release comparison](https://github.com/pre-commit/identify/compare/v2.6.18...v2.6.19) shows the user-visible change is recognition of `.tif` files, alongside maintenance-only changes. No breaking interface change is noted. Keep through `pre-commit`.

- **Ruff 0.16.3 to 0.16.4:** The official [0.16.4 release](https://github.com/astral-sh/ruff/releases/tag/0.16.4) fixes crashes on Windows CPUs without POPCNT, repairs string type-expression and parser edge cases, improves notebook diagnostics, and adjusts lint behavior. Duplicate keyword arguments are now syntax errors and several rules recognize more cases, so a previously clean tree can gain legitimate diagnostics. Ruff is pre-1.0 and patch releases may affect lint results. Keep if the repository's Ruff check passes; otherwise inspect the new finding rather than automatically rolling back.

- **ty 0.0.72 to 0.0.74:** The official [0.0.73](https://github.com/astral-sh/ty/releases/tag/0.0.73) and [0.0.74](https://github.com/astral-sh/ty/releases/tag/0.0.74) notes contain broad generic, protocol, solver, string-annotation, recursive `TypedDict`, LSP, and crash fixes. New diagnostics in this range are opt-in, though corrected inference may still change existing output. Keep if `ty check` passes; a failure should be reviewed as a possible newly detected type issue.

- **virtualenv 21.2.0 to 21.7.5:** The official [21.7.5 changelog](https://github.com/pypa/virtualenv/blob/21.7.5/docs/changelog.rst) spans wheel-extraction path and hash validation, download/HTTPS hardening, xonsh and GraalPy support, symlink/debug/free-threaded discovery, Python 3.15 support, and refreshed embedded seed packages. Version 21.5 drops Python 3.8 and refuses unsupported target versions unless seeding is disabled or delegated; 21.6 removes the old distutils import hook for Python 3.10 and newer. The project requires Python 3.14, so these support-floor changes are safe. Keep through `pre-commit`.

## Dependency graph explanation

- `detect-installer` is added solely because the upgraded `fastapi-cloud-cli 0.23.0` declares it.
- `distro` and `tqdm` disappear solely because `openai 3.3.1` no longer declares them. They are not direct project dependencies and are not imported in repository code.
- `jiter` remains an OpenAI dependency, with OpenAI raising its lower bound to 0.16.0.
- `packaging` remains because pytest requires it. Gunicorn's official manifests show it as a core dependency in [26.0.0](https://github.com/benoitc/gunicorn/blob/26.0.0/pyproject.toml) but only in optional development groups in [26.2.0](https://github.com/benoitc/gunicorn/blob/26.2.0/pyproject.toml).
- `platformdirs` remains because `virtualenv` requires it, even though `python-discovery 1.5.1` removed its own runtime dependency on it.
- `distlib`, `filelock`, `platformdirs`, and `python-discovery` are reached through `virtualenv`; `identify` and `virtualenv` are reached through `pre-commit`; `rignore` and `detect-installer` are reached through `fastapi-cloud-cli`; and Pygments is reached through pytest and Rich.

## Final recommendation

Keep every selected working-tree version and keep both removals. All 16 additions or upgrades are at least 72 hours old under the exact cutoff recorded in `uv.lock`. No package in this assigned set needs an age-based rollback. The only deployment checks warranted by release behavior are the existing Ruff and ty jobs, plus awareness of Sentry's deprecated logging/metrics switches if deployment configuration outside the repository supplies them.
