from datetime import datetime

from app.constants import PREFIX, VLR_IMAGE


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
