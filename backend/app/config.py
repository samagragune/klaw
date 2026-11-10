"""Application configuration.

Everything is environment driven so the same code runs against SQLite + an
in-process event bus locally, and Postgres + Redis in Docker/production.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    reliability_env: str = "development"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    secret_key: str = "change-me-generate-a-random-value"

    database_url: str = "sqlite+aiosqlite:///./data/reliability.db"

    event_bus: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"

    dev_user_email: str = "dev@reliability.local"
    dev_session_token: str = "local-dev-token-change-me"

    supervisor_llm_provider: Literal["gemini", "openai", "anthropic", "fake", "none"] = "none"
    supervisor_model: str = "gemini-2.0-flash"
    supervisor_llm_api_key: str = ""
    supervisor_llm_base_url: str = ""

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # How long a control long-poll request is held open before returning empty.
    control_poll_timeout_seconds: float = 25.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_sqlite:
        # Make sure the directory for the SQLite file exists before the engine
        # tries to open it.
        raw = settings.database_url.split("///", 1)[-1]
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
    return settings
