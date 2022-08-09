from pydantic import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str | None
    SENTRY_DSN: str | None

    class Config:
        env_file = ".env"


settings = Settings()
