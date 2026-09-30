"""Application configuration via Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Database (Supabase PostgreSQL)
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/adas"

    # API
    app_name: str = "Autonomous Data Analytics Swarm"
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Agent / orchestration
    max_agent_retries: int = 3

    # Docker sandbox for agent-generated code
    docker_image: str = "python:3.11-slim"
    docker_timeout_seconds: int = 15
    docker_memory_limit: str = "512m"
    docker_vendor_volume: str = "adas_sandbox_vendor"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
