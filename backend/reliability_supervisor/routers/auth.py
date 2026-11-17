"""Dashboard accounts: sign up, sign in, who am I."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from reliability_supervisor.config import get_settings
from reliability_supervisor.models import User
from reliability_supervisor.schemas import GoogleLoginIn, LoginIn, SessionTokenOut, SignupIn
from reliability_supervisor.security import (
    CurrentUser,
    DbSession,
    hash_password,
    issue_session_token,
    record_audit,
    verify_password,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _user_view(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name}


@router.post("/signup", response_model=SessionTokenOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, session: DbSession) -> SessionTokenOut:
    if not get_settings().allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is disabled on this deployment")
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address")
    exists = (
        await session.execute(select(User.id).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    session.add(user)
    await session.flush()
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="account.created",
        target_type="user",
        target_id=user.id,
    )
    await session.commit()
    token, expires = issue_session_token(user.id)
    return SessionTokenOut(token=token, expires_at=expires, user=_user_view(user))


@router.post("/login", response_model=SessionTokenOut)
async def login(body: LoginIn, session: DbSession) -> SessionTokenOut:
    user = (
        await session.execute(
            select(User).where(func.lower(User.email) == body.email.strip().lower())
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash if user else None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    token, expires = issue_session_token(user.id)
    return SessionTokenOut(token=token, expires_at=expires, user=_user_view(user))


@router.get("/me")
async def me(user: CurrentUser) -> dict[str, Any]:
    return _user_view(user)


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------

GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"]
_jwks_client = None


def verify_google_credential(credential: str, allowed_audiences: list[str]) -> dict[str, Any]:
    """Verify a Google Identity Services ID token. Raises ValueError."""
    import jwt  # PyJWT

    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GOOGLE_CERTS, cache_keys=True, lifespan=3600)
    try:
        key = _jwks_client.get_signing_key_from_jwt(credential).key
        claims = jwt.decode(
            credential,
            key,
            algorithms=["RS256"],
            audience=allowed_audiences,
            issuer=GOOGLE_ISSUERS,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"Google sign-in token rejected: {exc}") from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise ValueError("Google account has no verified email address")
    return claims


@router.get("/providers")
async def providers() -> dict[str, Any]:
    """Which sign-in methods this supervisor accepts (public)."""
    settings = get_settings()
    return {
        "email": True,
        "signup": settings.allow_signup,
        "google_client_ids": settings.google_client_id_list,
    }


@router.post("/google", response_model=SessionTokenOut)
async def google_login(body: GoogleLoginIn, session: DbSession) -> SessionTokenOut:
    settings = get_settings()
    allowed = settings.google_client_id_list
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google sign-in is not enabled on this supervisor")
    try:
        claims = verify_google_credential(body.credential, allowed)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    email = str(claims["email"]).strip().lower()
    user = (
        await session.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None:
        if not settings.allow_signup:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is disabled on this supervisor")
        user = User(email=email, name=claims.get("name"))
        session.add(user)
        await session.flush()
        await record_audit(
            session,
            user_id=user.id,
            actor="user",
            action="account.created",
            target_type="user",
            target_id=user.id,
            detail={"via": "google"},
        )
        await session.commit()
    token, expires = issue_session_token(user.id)
    return SessionTokenOut(token=token, expires_at=expires, user=_user_view(user))
