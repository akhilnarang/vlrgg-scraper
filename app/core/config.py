import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_string_list(value: object, setting: str) -> list[str]:
    """Normalize a JSON list or comma-separated environment value.

    :param value: Environment value or Python list.
    :param setting: Setting name for validation errors.
    :return: Nonempty, stripped strings.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value) if value.lstrip().startswith("[") else value.split(",")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{setting} must be a list of strings or comma-separated string")
    return [item.strip() for item in value if item.strip()]


class Settings(BaseSettings):
    API_KEYS: dict = {}
    INTERNAL_API_KEY: str

    SENTRY_DSN: str | None = None

    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str

    DATABASE_URL: str = "sqlite+aiosqlite:///db.sqlite3"

    ENABLE_CACHE: bool = False
    ENABLE_ID_MAP_DB: bool = False

    ENABLE_LIVE_PUSH: bool = False
    APNS_CREDENTIALS_FILE: str | None = None
    GOOGLE_APPLICATION_CREDENTIALS: str | None = None

    USER_AGENTS: Annotated[list[str], NoDecode] = ["val-esports-app/1.0"]
    HTTP_LOCAL_ADDRESSES: Annotated[list[str], NoDecode] = []

    TIMEZONE: str

    LLM_API_KEY: str | None = None
    LLM_BASE_URL: str | None = None
    LLM_MODEL: str = "gpt-5.4"
    LLM_REASONING_EFFORT: str | None = "low"
    LLM_DEBUG: bool = False
    LLM_MAX_STEPS: int = 10
    LLM_MAX_TOOL_CALLS: int = 12
    LLM_RATE_LIMIT_ENABLED: bool = False
    LLM_RATE_LIMIT: int = 5
    LLM_RATE_LIMIT_WINDOW: int = 60

    model_config = SettingsConfigDict(env_file=".env")

    @field_validator("USER_AGENTS", mode="before")
    @classmethod
    def parse_user_agents(cls, value: object) -> list[str]:
        """Normalize configured user agents from an environment value.

        JSON is the reliable format for user agents containing commas.

        :param value: Comma-separated user agents or a JSON list of user agents.
        :return: Nonempty, stripped user agents.
        """
        return _parse_string_list(value, "USER_AGENTS")

    @field_validator("HTTP_LOCAL_ADDRESSES", mode="before")
    @classmethod
    def parse_http_local_addresses(cls, value: object) -> list[str]:
        """Normalize local interface addresses from an environment value.

        :param value: Comma-separated addresses or a JSON list of addresses.
        :return: Nonempty, stripped addresses.
        """
        return _parse_string_list(value, "HTTP_LOCAL_ADDRESSES")

    @property
    def needs_redis(self) -> bool:
        """Return whether an enabled feature needs Redis."""
        return self.ENABLE_CACHE or self.ENABLE_LIVE_PUSH


settings = Settings()
