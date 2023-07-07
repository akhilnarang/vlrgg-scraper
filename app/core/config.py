from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SECRET_KEY: str | None = None
    SENTRY_DSN: str | None = None

    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str

    GOOGLE_APPLICATION_CREDENTIALS: str | None

    TIMEZONE: str
    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
