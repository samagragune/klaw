"""Application configuration.

Everything is environment driven so the same code runs against SQLite + an
in-process event bus locally, and Postgres + Redis in Docker/production.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
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
    supervisor_model: str = "gemini-3.5-flash"
    #: Cheaper model for routine screening; empty = use supervisor_model.
    supervisor_fast_model: str = ""
    supervisor_llm_api_key: str = ""
    supervisor_llm_base_url: str = ""
    #: Accepted as a fallback for SUPERVISOR_LLM_API_KEY when the provider is
    #: gemini, so one key in .env serves both the supervisor and the demo agents.
    gemini_api_key: str = ""

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    #: Public URL to ping so a free-tier host never idles (see keepalive.py).
    #: On Render this falls back to RENDER_EXTERNAL_URL automatically.
    keepalive_url: str = ""
    #: Render sleeps after ~15 min idle; 10 min keeps a safe margin.
    keepalive_interval_seconds: float = 600.0

    #: Open sign-up on the dashboard. Turn off on a private deployment once
    #: the accounts you need exist.
    allow_signup: bool = True

    # How long a control long-poll request is held open before returning empty.
    control_poll_timeout_seconds: float = 25.0
    # Silence (no events, no control polls) after which a running execution
    # is presumed dead and closed as failed. Must comfortably exceed the
    # SDK's poll interval.
    stale_execution_seconds: float = 180.0
    #: Silence after which a session from a platform with no liveness signal
    #: (Claude Code) is marked inactive. Never marked failed.
    inactive_session_seconds: float = 1800.0

    @model_validator(mode="after")
    def _normalise_database_url(self) -> "Settings":
        # Hosts (Railway, Fly, Render, Heroku) hand out postgres:// URLs;
        # SQLAlchemy's async engine needs the asyncpg driver named.
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                self.database_url = "postgresql+asyncpg://" + url[len(prefix):]
                break
        return self

    @model_validator(mode="after")
    def _fallback_provider_key(self) -> "Settings":
        if not self.supervisor_llm_api_key and self.supervisor_llm_provider == "gemini":
            self.supervisor_llm_api_key = self.gemini_api_key
        return self

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
