from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sentry_sdk.types import Event, Hint

from app.constants import PREFIX, VLR_IMAGE
from app.core.config import settings


def get_image_url(img: str) -> str:
    """
    Determine an image URL based on the string
    :param img: The src string of the image
    :return: The full URL
    """
    if img.startswith(VLR_IMAGE):
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
    return value.replace(tzinfo=ZoneInfo(settings.TIMEZONE)).astimezone(ZoneInfo("UTC"))


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
        # Ensure that the value is not "nan"
        if value != "nan":
            # Remove the percentage sign if it exists
            if value[-1] == "%":
                value = value[:-1]
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    pass
    return 0


def add_protocol_to_url(url: str) -> str:
    """
    Function to add a protocol to a URL if it does not already have one

    :param url: The URL to check
    :return: The URL with a protocol
    """
    if not url.startswith("http"):
        return f"https://{url}"
    return url


def before_send(event: Event, hint: Hint) -> Event | None:
    """
    Function to strip out HTTPExceptions from Sentry reports

    :param event: Sentry event
    :param hint: Sentry event hint
    :return: The event if we want it to be uploaded, else nothing
    """
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, HTTPException):
            return None
    return event
