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

    GOOGLE_APPLICATION_CREDENTIALS: str | None = None

    TIMEZONE: str
    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
