"""Database engine and session management."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import PROJECT_ROOT, get_settings


class Base(DeclarativeBase):
    pass


#: libpq query parameters asyncpg does not understand. Hosted Postgres
#: (Neon, Render, Supabase) puts these in the connection string it hands out.
_LIBPQ_ONLY = {"sslmode", "channel_binding", "options", "target_session_attrs"}


def resolve_database(url: str, is_sqlite: bool) -> tuple[str, dict]:
    """Return ``(url, connect_args)`` ready for the async engine."""
    if is_sqlite:
        prefix, _, raw = url.partition("///")
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return f"{prefix}///{path.as_posix()}", {}

    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = next((v for k, v in query if k == "sslmode"), None)
    kept = [(k, v) for k, v in query if k not in _LIBPQ_ONLY]
    clean = urlunsplit(parts._replace(query=urlencode(kept)))
    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require" if sslmode == "require" else True
    return clean, connect_args


def _engine_args() -> tuple[str, dict]:
    settings = get_settings()
    return resolve_database(settings.database_url, settings.is_sqlite)


_url, _connect_args = _engine_args()
engine = create_async_engine(
    _url,
    echo=False,
    future=True,
    connect_args=_connect_args,
    # Hosted Postgres drops idle connections (Neon suspends compute); check
    # before use instead of failing the first request after a quiet spell.
    pool_pre_ping=not get_settings().is_sqlite,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Create tables that do not exist yet.

    This project uses create_all rather than migrations while the schema is
    still moving. Alembic should be introduced before any deployment that
    carries data worth keeping.
    """
    from app import models  # noqa: F401  (import registers mappers)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
