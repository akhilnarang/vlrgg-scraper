from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def fix_datetime_tz(value: datetime) -> str:
    return value.replace(tzinfo=ZoneInfo(settings.TIMEZONE)).astimezone(ZoneInfo("UTC")).isoformat()
