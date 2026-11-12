"""Every supervisor-LLM call goes through ``ask``.

It does three things the rest of the code should never have to think about:

* **Records** the call (purpose, model, tokens, cost, latency, success) so the
  dashboard can show what supervision itself costs.
* **Caches** by a hash of (model, system prompt, prompt). The prompt is built
  from stored evidence, so an identical prompt is an identical question - a
  re-evaluation of an unchanged task costs nothing and is recorded as cached.
* **Parses** the JSON answer leniently.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvaluatorCall, utcnow
from app.pricing import estimate_cost
from app.supervision.providers import LLMProvider

CACHE_TTL = timedelta(hours=24)


def parse_json(text: str) -> dict[str, Any]:
    """Parse a model response, tolerating fenced code blocks."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        value = json.loads(cleaned.strip())
    except json.JSONDecodeError:
        return {"summary": cleaned.strip()[:2000], "confidence": "unparsed"}
    return value if isinstance(value, dict) else {"summary": str(value)}


async def ask(
    session: AsyncSession | None,
    *,
    user_id: str | None,
    purpose: str,
    provider: LLMProvider,
    system: str,
    prompt: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    key = hashlib.sha256(
        f"{provider.name}\x00{provider.model}\x00{system}\x00{prompt}".encode("utf-8")
    ).hexdigest()

    if session is not None and user_id and use_cache:
        hit = (
            await session.execute(
                select(EvaluatorCall)
                .where(EvaluatorCall.user_id == user_id)
                .where(EvaluatorCall.cache_key == key)
                .where(EvaluatorCall.ok.is_(True))
                .where(EvaluatorCall.cached.is_(False))
                .where(EvaluatorCall.created_at >= utcnow() - CACHE_TTL)
                .order_by(EvaluatorCall.created_at.desc())
            )
        ).scalars().first()
        if hit is not None and hit.result is not None:
            session.add(
                EvaluatorCall(
                    user_id=user_id,
                    purpose=purpose,
                    model=provider.model,
                    cached=True,
                    cache_key=key,
                    result=hit.result,
                )
            )
            return dict(hit.result)

    started = time.perf_counter()
    ok = False
    result: dict[str, Any] | None = None
    try:
        raw = await provider.complete(system, prompt)
        result = parse_json(raw)
        ok = result.get("confidence") != "unparsed"
        return result
    finally:
        if session is not None and user_id:
            prompt_tokens, completion_tokens = getattr(provider, "last_usage", (0, 0))
            session.add(
                EvaluatorCall(
                    user_id=user_id,
                    purpose=purpose,
                    model=provider.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    estimated_cost=estimate_cost(
                        provider.model, prompt_tokens, completion_tokens
                    ),
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    ok=ok,
                    cache_key=key,
                    result=result if ok else None,
                )
            )
