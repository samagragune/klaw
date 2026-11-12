"""Dashboard accounts: sign up, sign in, who am I."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.config import get_settings
from app.models import User
from app.schemas import LoginIn, SessionTokenOut, SignupIn
from app.security import (
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
