"""Integration registry: connect an agent environment, scope its key."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.models import (
    PLATFORM_CAPABILITIES,
    Agent,
    ApiKey,
    Capability,
    Execution,
    ExecutionStatus,
    Integration,
    utcnow,
)
from app.schemas import (
    IntegrationConnectIn,
    IntegrationCreate,
    IntegrationCreated,
    IntegrationOut,
)
from app.security import (
    INTEGRATION_KEY_PREFIX,
    CurrentPrincipal,
    CurrentUser,
    DbSession,
    generate_api_key,
    record_audit,
)

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

PLATFORM_LABELS = {
    "claude_code": "Claude Code",
    "openai_agents": "OpenAI Agents SDK",
    "custom_sdk": "Custom Python SDK",
}

#: Honest statements of what each platform can and cannot do, shown on the
#: dashboard next to the integration.
PLATFORM_LIMITS = {
    "claude_code": [
        "Cannot be paused mid-turn: hooks can block a tool call, hold it for "
        "approval (bounded by the hook timeout), stop the session or return "
        "feedback - nothing can suspend a running turn.",
        "Control applies at the next tool call or completion; a long-running "
        "Bash command is not interrupted.",
        "Hooks report no token usage, so token and cost budgets do not apply.",
    ],
    "openai_agents": [
        "Control applies at run-hook boundaries (before a model call or tool "
        "call); a single in-flight model call is not interrupted.",
        "Hosted tools (web search, file search) run on OpenAI's side and are "
        "observed through tracing, not intercepted.",
        "Supported for applications built with the OpenAI Agents SDK only, not "
        "every application that calls OpenAI models.",
    ],
    "custom_sdk": [
        "Control applies at SDK checkpoints (tool(), llm(), checkpoint()).",
    ],
}


async def _owned(session, user, integration_id: str) -> Integration:
    integration = (
        await session.execute(
            select(Integration)
            .where(Integration.id == integration_id)
            .where(Integration.user_id == user.id)
        )
    ).scalar_one_or_none()
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    return integration


@router.get("/platforms")
async def platforms() -> list[dict[str, Any]]:
    return [
        {
            "platform": p,
            "label": PLATFORM_LABELS[p],
            "capabilities": list(caps),
            "limitations": PLATFORM_LIMITS[p],
        }
        for p, caps in PLATFORM_CAPABILITIES.items()
    ]


@router.post("", response_model=IntegrationCreated, status_code=status.HTTP_201_CREATED)
async def create_integration(
    body: IntegrationCreate, user: CurrentUser, session: DbSession
) -> IntegrationCreated:
    integration = Integration(
        user_id=user.id,
        platform=body.platform,
        name=body.name,
        capabilities=list(PLATFORM_CAPABILITIES[body.platform]),
    )
    session.add(integration)
    await session.flush()
    plaintext, digest, prefix = generate_api_key(INTEGRATION_KEY_PREFIX)
    session.add(
        ApiKey(
            user_id=user.id,
            name=f"integration:{body.name}"[:120],
            key_hash=digest,
            prefix=prefix,
            integration_id=integration.id,
        )
    )
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="integration.created",
        target_type="integration",
        target_id=integration.id,
        detail={"platform": body.platform, "name": body.name},
    )
    await session.commit()
    return IntegrationCreated(
        **IntegrationOut.model_validate(integration).model_dump(), key=plaintext
    )


@router.get("")
async def list_integrations(user: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(Integration)
                .where(Integration.user_id == user.id)
                .order_by(Integration.created_at.desc())
            )
        ).scalars()
    )
    agent_counts = dict(
        (
            await session.execute(
                select(Agent.integration_id, func.count(Agent.id))
                .where(Agent.user_id == user.id)
                .group_by(Agent.integration_id)
            )
        ).all()
    )
    live_counts = dict(
        (
            await session.execute(
                select(Execution.integration_id, func.count(Execution.id))
                .where(Execution.user_id == user.id)
                .where(Execution.status.in_(tuple(ExecutionStatus.LIVE)))
                .group_by(Execution.integration_id)
            )
        ).all()
    )
    now = utcnow()
    out = []
    for i in rows:
        seen = i.last_seen_at
        out.append(
            {
                **IntegrationOut.model_validate(i).model_dump(mode="json"),
                "label": PLATFORM_LABELS.get(i.platform, i.platform),
                "limitations": PLATFORM_LIMITS.get(i.platform, []),
                "agents": agent_counts.get(i.id, 0),
                "live_sessions": live_counts.get(i.id, 0),
                "connection": (
                    "revoked"
                    if i.revoked_at
                    else "never_connected"
                    if seen is None
                    else "active"
                    if (now - seen).total_seconds() < 300
                    else "quiet"
                ),
            }
        )
    return out


@router.post("/connect", response_model=IntegrationOut)
async def connect(
    body: IntegrationConnectIn, principal: CurrentPrincipal, session: DbSession
) -> Integration:
    """Called by an adapter with its integration key on start-up.

    The adapter declares what it can do; anything not supported by the
    platform at all is dropped, so an adapter cannot claim powers it lacks.
    """
    if principal.integration is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Connect with an integration key (rl_int_...)"
        )
    integration = principal.integration
    allowed = set(PLATFORM_CAPABILITIES.get(integration.platform, ())) | {Capability.OBSERVE}
    declared = [c for c in body.capabilities if c in allowed] or list(allowed)
    integration.capabilities = sorted(set(declared))
    integration.details = {**(integration.details or {}), **body.details}
    integration.last_seen_at = utcnow()
    await session.commit()
    return integration


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_integration(integration_id: str, user: CurrentUser, session: DbSession) -> None:
    integration = await _owned(session, user, integration_id)
    integration.revoked_at = utcnow()
    for key in (
        await session.execute(select(ApiKey).where(ApiKey.integration_id == integration.id))
    ).scalars():
        key.revoked_at = key.revoked_at or utcnow()
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="integration.revoked",
        target_type="integration",
        target_id=integration.id,
    )
    await session.commit()
