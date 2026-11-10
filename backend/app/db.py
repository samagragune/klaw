"""Database engine and session management."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import PROJECT_ROOT, get_settings


class Base(DeclarativeBase):
    pass


def _resolved_url() -> str:
    settings = get_settings()
    url = settings.database_url
    if settings.is_sqlite:
        prefix, _, raw = url.partition("///")
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        url = f"{prefix}///{path.as_posix()}"
    return url


engine = create_async_engine(_resolved_url(), echo=False, future=True)
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
