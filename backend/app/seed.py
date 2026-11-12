"""Create the local development user and an API key.

Run once after setting up .env::

    python -m app.seed

Prints an API key exactly once. It is stored only as a hash, so a lost key
cannot be recovered - run the script again with --new-key to mint another.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_models
from app.models import ApiKey, User
from app.security import generate_api_key


async def seed(mint_new_key: bool) -> None:
    settings = get_settings()
    await init_models()

    async with SessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.email == settings.dev_user_email)
            )
        ).scalar_one_or_none()
        created_user = user is None
        if user is None:
            user = User(email=settings.dev_user_email)
            session.add(user)
            await session.flush()

        existing = list(
            (
                await session.execute(
                    select(ApiKey)
                    .where(ApiKey.user_id == user.id)
                    .where(ApiKey.revoked_at.is_(None))
                )
            ).scalars()
        )

        plaintext = None
        if mint_new_key or not existing:
            plaintext, digest, prefix = generate_api_key()
            session.add(
                ApiKey(user_id=user.id, name="local-dev", key_hash=digest, prefix=prefix)
            )

        await session.commit()

        print()
        print("  Reliability - local setup")
        print("  " + "-" * 60)
        print(f"  user           {user.email} ({'created' if created_user else 'existing'})")
        print(f"  user_id        {user.id}")
        print(f"  database       {settings.database_url}")
        print(f"  event bus      {settings.event_bus}")
        print(f"  evaluator      {settings.supervisor_llm_provider}")
        print()
        if plaintext:
            print("  API key (shown once - copy it into .env as RELIABILITY_API_KEY):")
            print(f"    {plaintext}")
        else:
            print(f"  {len(existing)} active API key(s) already exist.")
            print("  Re-run with --new-key to mint another.")
        print()
        print("  Dashboard token (DEV_SESSION_TOKEN in .env):")
        print(f"    {settings.dev_session_token}")
        print()


async def set_password(email: str, password: str) -> None:
    """Give an account a dashboard password (creating the account if needed)."""
    from app.security import hash_password

    await init_models()
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=email)
            session.add(user)
        user.password_hash = hash_password(password)
        await session.commit()
    print(f"  password set for {email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the local Reliability user.")
    parser.add_argument(
        "--new-key", action="store_true", help="Mint an additional API key."
    )
    parser.add_argument(
        "--set-password",
        metavar="EMAIL",
        help="Set a dashboard password for EMAIL, read from the "
        "RELIABILITY_SET_PASSWORD environment variable (never from argv).",
    )
    args = parser.parse_args()
    if args.set_password:
        import os

        password = os.environ.get("RELIABILITY_SET_PASSWORD", "")
        if len(password) < 10:
            raise SystemExit("Set RELIABILITY_SET_PASSWORD (10+ characters).")
        asyncio.run(set_password(args.set_password, password))
        return
    asyncio.run(seed(args.new_key))


if __name__ == "__main__":
    main()
