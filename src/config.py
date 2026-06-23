from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    WARNING: Default DATABASE_URL and REDIS_URL use development credentials.
    Override via environment variables or .env file in production.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # DeepSeek API (empty default allows tests to import without .env)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Storage
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentforge"
    REDIS_URL: str = "redis://localhost:6379"

    # Application
    LOG_LEVEL: str = "INFO"
    TOY_REPO_PATH: str = "./toy-repo"

    # Loop Controller
    MAX_SPRINT_RETRIES: int = 3
    MAX_ITERATIONS: int = 10
    TOKEN_BUDGET: int = 100_000
    TIMEOUT_SECONDS: int = 300


settings = Settings()
