import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sentry_sdk.types import Event, Hint

from app.constants import PREFIX, VLR_IMAGE
from app.core.config import settings

_TZ_LOCAL = ZoneInfo(settings.TIMEZONE)
_TZ_UTC = ZoneInfo("UTC")


def get_image_url(img: str | list[str]) -> str:
    """
    Determine an image URL based on the string
    :param img: The src string of the image (or list from BeautifulSoup)
    :return: The full URL
    """
    if isinstance(img, list):
        img = img[0]
    if img.startswith("http"):
        return img
    elif img.startswith(VLR_IMAGE):
        return f"{PREFIX}{img}"
    else:
        return f"https:{img}"


def clear_datetime_tz(source: datetime) -> datetime:
    """
    Function that accepts a timezone-aware datetime object and strips out tzinfo
    :param source: A timezone-aware datetime object
    :return: A timezone-naive datetime object
    """
    return source.replace(tzinfo=None)


def fix_datetime_tz(value: datetime) -> datetime:
    return value.replace(tzinfo=_TZ_LOCAL).astimezone(_TZ_UTC)


def clean_string(value: str) -> str:
    """
    Function to clean a string, removing whitespaces, newlines and tabs

    :param value: The value to clean
    :return: The cleaned string
    """
    return value.strip().replace("\n", "").replace("\t", "")


def clean_number_string(value: str | None) -> int | float:
    """
    Function to clean a number string

    :param value: The value to clean
    :return: The cleaned integer/floating point value
    """
    if value and (value := clean_string(value)):
        if value != "nan":
            if value[-1] == "%":
                value = value[:-1]
            if "." in value:
                try:
                    return float(value)
                except ValueError:
                    pass
            else:
                try:
                    return int(value)
                except ValueError:
                    pass
    return 0


def expand_url(url: str | list[str] | None) -> str | None:
    """
    Function to expand a URL to full form

    :param url: The URL to expand (or list from BeautifulSoup)
    :return: The full URL, or None if invalid
    """
    if isinstance(url, list):
        url = url[0]
    if not url or not url.strip():
        return None
    if url.startswith("http"):
        return url
    elif url.startswith("/"):
        return f"{PREFIX}{url}"
    else:
        return f"https://{url}"


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
        from app.exceptions import ScrapingError

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


def simplify_name(name: str) -> str:
    """
    Function to generate a key for a name, like a simple hash

    :param name: The name
    :return: The key
    """
    return name.lower().replace(" ", "_")


def get_href(value: str | list[str]) -> str:
    """
    Safely extract href string from BeautifulSoup attribute.

    :param value: The href attribute (str or AttributeValueList)
    :return: The href string
    """
    if isinstance(value, list):
        return value[0]
    return value


def get_class(value: str | list[str] | None, index: int = 0) -> str:
    """
    Safely extract class string from BeautifulSoup class attribute.

    :param value: The class attribute (str, list, or None from BeautifulSoup)
    :param index: The index to extract (only used if value is a list)
    :return: The class string or empty string
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if len(value) > index:
        return value[index]
    return ""
