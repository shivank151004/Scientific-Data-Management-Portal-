"""Application configuration loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings sourced from the environment and a .env file."""

    PROJECT_NAME: str = "FastAPI Telemetry Portal"
    ENVIRONMENT: str = "development"
    MONGO_URI: str = Field(..., description="MongoDB connection string")
    POSTGRES_URI: str = Field(..., description="PostgreSQL connection string (DSN)")

    # Optional tuning knobs with safe defaults
    MONGO_DB_NAME: str = "telemetry"
    MONGO_COLLECTION: str = "telemetry_events"
    POSTGRES_POOL_MIN_SIZE: int = 1
    POSTGRES_POOL_MAX_SIZE: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
