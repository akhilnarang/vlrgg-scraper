"""Logging and Sentry setup."""

import logging
import os
import re
import subprocess
from contextlib import suppress

import sentry_sdk
from fastapi import HTTPException
from rich.logging import RichHandler
from sentry_sdk.integrations.arq import ArqIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.httpx2 import Httpx2Integration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.types import Event, Hint

from app.core.config import settings
from app.exceptions import ScrapingError


def configure_logging() -> None:
    """Send logs through Rich and quiet the per-request httpx logs.

    :return: None.
    """
    logging.basicConfig(
        format="[%(levelname)s] (%(asctime)s) %(module)s:%(pathname)s:%(funcName)s:%(lineno)s:: %(message)s",
        level=logging.INFO,
        datefmt="%d-%m-%y %H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def git_release() -> str | None:
    """Find the release to report to Sentry.

    :return: `GIT_SHA` from the environment, else the checked-out commit, else None.
    """
    if release := os.environ.get("GIT_SHA"):
        return release
    with suppress(OSError, subprocess.CalledProcessError):
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    return None


def traces_sampler(sampling_context: dict) -> float:
    """100% sampling for cron jobs, 8% for API requests. Respects parent sampling decisions."""
    if (parent := sampling_context.get("parent_sampled")) is not None:
        return 1.0 if parent else 0.0
    if sampling_context.get("transaction_context", {}).get("op") == "queue.task.arq":
        return 1.0
    return 0.08


def before_send(event: Event, hint: Hint) -> Event | None:
    """
    Filter and enrich Sentry events.
    - Drop client errors (4xx) — not actionable
    - Keep server errors (5xx) — ScrapingError, InternalServerError, etc.
    - Enrich ScrapingError with upstream URL/status context
    """
    if "exc_info" in hint:
        exc_value = hint["exc_info"][1]
        if isinstance(exc_value, HTTPException) and exc_value.status_code < 500:
            return None

        # Enrich ScrapingError with scraping context for Sentry dashboard
        if isinstance(exc_value, ScrapingError):
            event.setdefault("contexts", {})["scraping"] = {
                "url": exc_value.url,
                "upstream_status": exc_value.upstream_status,
            }
            # Fingerprint by URL pattern — normalize IDs to {id} to avoid cardinality explosion
            url_pattern = exc_value.url.split("?")[0] if exc_value.url else "unknown"
            # Replace numeric path segments: /event/2760/foo → /event/{id}/foo
            url_pattern = re.sub(r"/\d+", "/{id}", url_pattern)
            event["fingerprint"] = [
                "scraping-error",
                str(exc_value.upstream_status or "unknown"),
                url_pattern,
            ]

    return event


def init_sentry() -> None:
    """Initialize Sentry when a DSN is configured.

    :return: None.
    """
    if not settings.SENTRY_DSN:
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        release=git_release(),
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            HttpxIntegration(),
            Httpx2Integration(),
            ArqIntegration(),
        ],
        traces_sampler=traces_sampler,
        before_send=before_send,
    )
