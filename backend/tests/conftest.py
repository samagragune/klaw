"""Test fixtures.

The environment is configured before ``app`` is imported, because the engine
and settings are built at import time. Tests run against their own SQLite
file and never touch the development database.
"""
from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent / "test_reliability.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["EVENT_BUS"] = "memory"
os.environ["DEV_SESSION_TOKEN"] = "test-dashboard-token"
os.environ["DEV_USER_EMAIL"] = "test@reliability.local"
os.environ["SUPERVISOR_LLM_PROVIDER"] = "none"
os.environ["SUPERVISOR_LLM_API_KEY"] = ""

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ApiKey, User  # noqa: E402
from app.security import generate_api_key  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_schema():
    """A clean schema per test, so ordering never matters."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def accounts() -> dict:
    """Two separate tenants, so isolation can be tested for real."""
    created = {}
    async with SessionLocal() as session:
        for label, email in (("primary", "test@reliability.local"), ("other", "other@example.com")):
            user = User(email=email)
            session.add(user)
            await session.flush()
            plaintext, digest, prefix = generate_api_key()
            session.add(
                ApiKey(user_id=user.id, name=label, key_hash=digest, prefix=prefix)
            )
            created[label] = {"user_id": user.id, "email": email, "key": plaintext}
        await session.commit()
    return created


@pytest_asyncio.fixture
async def client(accounts) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {accounts['primary']['key']}"},
    ) as c:
        c.accounts = accounts  # type: ignore[attr-defined]
        yield c


@pytest_asyncio.fixture
async def other_client(accounts) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {accounts['other']['key']}"},
    ) as c:
        yield c


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
