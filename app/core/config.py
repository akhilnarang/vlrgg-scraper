from pydantic import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str | None
    SENTRY_DSN: str | None

    REDIS_HOST: str
    REDIS_PASSWORD: str

    class Config:
        env_file = ".env"


settings = Settings()
