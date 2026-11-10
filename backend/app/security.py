"""Authentication, API keys and audit logging.

Two credential types reach the API, both via ``Authorization: Bearer <token>``:

* an **API key** (``rl_...``) used by the SDK. Stored only as a SHA-256 hash.
* the **dev session token** used by the local dashboard. Compared in constant
  time against the configured value. This is the deliberate MVP shortcut
  standing in for a login flow; the user resolution path below is the only
  place that changes when real sessions are added.

Everything downstream of ``current_user`` is scoped by ``user_id``, so
isolation does not depend on which credential type was used.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import ApiKey, AuditLog, User, utcnow

API_KEY_PREFIX = "rl_live_"


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(plaintext, hash, display_prefix)``.

    The plaintext is returned to the caller exactly once and never stored.
    """
    secret = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_PREFIX}{secret}"
    return plaintext, hash_api_key(plaintext), plaintext[: len(API_KEY_PREFIX) + 4]


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def resolve_user(token: str, session: AsyncSession) -> User:
    """Resolve a bearer token to a user, or raise 401."""
    settings = get_settings()

    if token.startswith(API_KEY_PREFIX):
        digest = hash_api_key(token)
        key = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == digest))
        ).scalar_one_or_none()
        if key is None or key.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key")
        key.last_used_at = utcnow()
        user = (
            await session.execute(select(User).where(User.id == key.user_id))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key has no owner")
        await session.commit()
        return user

    configured = settings.dev_session_token
    if configured and hmac.compare_digest(token, configured):
        user = (
            await session.execute(select(User).where(User.email == settings.dev_user_email))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Dev user has not been seeded. Run: python -m app.seed",
            )
        return user

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> User:
    return await resolve_user(_extract_bearer(authorization), session)


CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


async def record_audit(
    session: AsyncSession,
    *,
    user_id: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit entry. Caller commits."""
    entry = AuditLog(
        user_id=user_id,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
    )
    session.add(entry)
    return entry
