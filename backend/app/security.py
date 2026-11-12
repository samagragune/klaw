"""Authentication, credentials and audit logging.

Credentials, all sent as ``Authorization: Bearer <token>``:

* **User API key** (``rl_live_...``) - full access to the owner's workspace.
  Used by the custom SDK and scripts. Stored as a SHA-256 hash.
* **Integration key** (``rl_int_...``) - scoped to one integration. It can
  send events, request decisions, wait on approvals and poll control for that
  integration's sessions, and nothing else. This is what is installed on a
  developer's machine for Claude Code, so a leaked key cannot read the
  dashboard or change policy.
* **Session token** (``rs_...``) - issued by ``/v1/auth/login`` to the
  dashboard. Signed with ``SECRET_KEY``, expires; nothing is stored.
* **Dev session token** - the fixed local token from ``.env``. Accepted only
  when ``RELIABILITY_ENV=development``; a deployed backend refuses it.

Everything downstream is scoped by ``user_id``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import ApiKey, AuditLog, Integration, User, utcnow

API_KEY_PREFIX = "rl_live_"
INTEGRATION_KEY_PREFIX = "rl_int_"
SESSION_TOKEN_PREFIX = "rs_"
SESSION_TTL_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def generate_api_key(prefix: str = API_KEY_PREFIX) -> tuple[str, str, str]:
    """Return ``(plaintext, hash, display_prefix)``. Plaintext is shown once."""
    plaintext = f"{prefix}{secrets.token_urlsafe(32)}"
    return plaintext, hash_api_key(plaintext), plaintext[: len(prefix) + 4]


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Passwords (stdlib scrypt - no extra dependency)
# ---------------------------------------------------------------------------

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${digest}".format(
        **_SCRYPT, salt=salt.hex(), digest=digest.hex()
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Spend comparable time anyway so timing does not reveal which
        # accounts have a password.
        hashlib.scrypt(b"x", salt=b"0" * 16, dklen=32, **_SCRYPT)
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            dklen=32,
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_session_token(user_id: str, ttl: int = SESSION_TTL_SECONDS) -> tuple[str, datetime]:
    expires = int(time.time()) + ttl
    body = _b64(json.dumps({"uid": user_id, "exp": expires}, separators=(",", ":")).encode())
    sig = _b64(
        hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256).digest()
    )
    return f"{SESSION_TOKEN_PREFIX}{body}.{sig}", datetime.fromtimestamp(expires, timezone.utc)


def read_session_token(token: str) -> str | None:
    """Return the user id for a valid, unexpired token."""
    try:
        body, sig = token[len(SESSION_TOKEN_PREFIX):].split(".", 1)
    except ValueError:
        return None
    expected = _b64(
        hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(claims.get("exp", 0)) < time.time():
        return None
    return claims.get("uid")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@dataclass
class Principal:
    user: User
    #: Set when authenticated with an integration-scoped key.
    integration: Integration | None
    via: str  # "api_key" | "integration_key" | "session" | "dev"

    def __post_init__(self) -> None:
        # Plain copies: ORM objects expire on rollback, and a long-poll
        # handler rolls back to see other requests' writes.
        self.user_id: str = self.user.id
        self.integration_id: str | None = self.integration.id if self.integration else None

    @property
    def actor(self) -> str:
        if self.via == "integration_key" and self.integration is not None:
            return f"integration:{self.integration.platform}"
        return "user" if self.via in ("session", "dev") else "api_key"


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


async def _user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credential has no owner")
    return user


async def resolve_principal(token: str, session: AsyncSession) -> Principal:
    settings = get_settings()

    if token.startswith(API_KEY_PREFIX) or token.startswith(INTEGRATION_KEY_PREFIX):
        key = (
            await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)))
        ).scalar_one_or_none()
        if key is None or key.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked key")
        integration = None
        if key.integration_id:
            integration = await session.get(Integration, key.integration_id)
            if integration is None or integration.revoked_at is not None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Integration was revoked")
            integration.last_seen_at = utcnow()
        key.last_used_at = utcnow()
        user = await _user(session, key.user_id)
        await session.commit()
        return Principal(
            user=user,
            integration=integration,
            via="integration_key" if integration else "api_key",
        )

    if token.startswith(SESSION_TOKEN_PREFIX):
        user_id = read_session_token(token)
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired; sign in again")
        return Principal(user=await _user(session, user_id), integration=None, via="session")

    configured = settings.dev_session_token
    if (
        settings.reliability_env == "development"
        and configured
        and hmac.compare_digest(token, configured)
    ):
        user = (
            await session.execute(select(User).where(User.email == settings.dev_user_email))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Dev user has not been seeded. Run: python -m app.seed",
            )
        return Principal(user=user, integration=None, via="dev")

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


async def resolve_user(token: str, session: AsyncSession) -> User:
    """Back-compat helper (WebSocket auth). Integration keys are refused."""
    principal = await resolve_principal(token, session)
    if principal.integration is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Integration keys cannot use the management API"
        )
    return principal.user


async def current_principal(
    authorization: Annotated[str | None, Header()] = None,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> Principal:
    return await resolve_principal(_extract_bearer(authorization), session)


async def current_user(
    principal: Annotated[Principal, Depends(current_principal)],
) -> User:
    """Management API: workspace credentials only."""
    if principal.integration is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Integration keys can only send events, request decisions and poll "
            "control. Use a user key or sign in for the management API.",
        )
    return principal.user


CurrentUser = Annotated[User, Depends(current_user)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
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
