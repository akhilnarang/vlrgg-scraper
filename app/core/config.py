from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    API_KEYS: dict = {}
    INTERNAL_API_KEY: str

    SENTRY_DSN: str | None = None

    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str

    ENABLE_CACHE: bool = False
    ENABLE_ID_MAP_DB: bool = False
    # Live match SSE is on by default. It uses Redis keys that are separate
    # from the response cache, so it needs Redis but not ENABLE_CACHE.
    ENABLE_LIVE_MATCHES: bool = True

    GOOGLE_APPLICATION_CREDENTIALS: str | None = None

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

    @property
    def needs_redis(self) -> bool:
        """Report whether response caching or live matches need Redis.

        :return: ``True`` when at least one Redis feature is on.
        """
        return self.ENABLE_CACHE or self.ENABLE_LIVE_MATCHES


settings = Settings()  # type: ignore
