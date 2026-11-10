"""Overview metrics, API key management and the audit log.

Every number here is computed from stored rows. Nothing is simulated, and a
figure that cannot be computed completely is reported as incomplete rather
than rounded into something that looks authoritative.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.config import get_settings
from app.models import (
    Agent,
    AgentStatus,
    ApiKey,
    AuditLog,
    Execution,
    ExecutionStatus,
    Incident,
    IncidentStatus,
    utcnow,
)
from app.schemas import ApiKeyCreated, ApiKeyOut, OverviewMetrics
from app.security import CurrentUser, DbSession, generate_api_key, record_audit
from app.supervision.providers import provider_configured

router = APIRouter(prefix="/v1", tags=["metrics"])


@router.get("/metrics/overview", response_model=OverviewMetrics)
async def overview(user: CurrentUser, session: DbSession) -> OverviewMetrics:
    agent_rows = (
        await session.execute(
            select(Agent.status, func.count(Agent.id))
            .where(Agent.user_id == user.id)
            .group_by(Agent.status)
        )
    ).all()
    agents_by_status = {row[0]: row[1] for row in agent_rows}

    exec_rows = (
        await session.execute(
            select(Execution.status, func.count(Execution.id))
            .where(Execution.user_id == user.id)
            .group_by(Execution.status)
        )
    ).all()
    execs_by_status = {row[0]: row[1] for row in exec_rows}

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(Execution.token_usage), 0),
                func.coalesce(func.sum(Execution.estimated_cost), 0.0),
                func.count(Execution.id).filter(Execution.cost_complete.is_(False)),
            ).where(Execution.user_id == user.id)
        )
    ).one()

    open_incidents = (
        await session.execute(
            select(func.count(Incident.id))
            .where(Incident.user_id == user.id)
            .where(Incident.status != IncidentStatus.RESOLVED)
        )
    ).scalar_one()

    return OverviewMetrics(
        total_agents=sum(agents_by_status.values()),
        running_agents=agents_by_status.get(AgentStatus.RUNNING, 0)
        + agents_by_status.get(AgentStatus.WARNING, 0),
        running_executions=execs_by_status.get(ExecutionStatus.RUNNING, 0)
        + execs_by_status.get(ExecutionStatus.PAUSED, 0),
        executions_total=sum(execs_by_status.values()),
        executions_succeeded=execs_by_status.get(ExecutionStatus.COMPLETED, 0),
        executions_failed=execs_by_status.get(ExecutionStatus.FAILED, 0),
        executions_cancelled=execs_by_status.get(ExecutionStatus.CANCELLED, 0),
        open_incidents=int(open_incidents or 0),
        total_tokens=int(totals[0] or 0),
        estimated_cost=float(totals[1] or 0.0),
        cost_complete=int(totals[2] or 0) == 0,
    )


@router.get("/config")
async def runtime_config(user: CurrentUser) -> dict[str, Any]:
    """What the dashboard needs to know about this deployment.

    Exposes capability flags only - never a key, never a connection string.
    """
    settings = get_settings()
    return {
        "environment": settings.reliability_env,
        "event_bus": settings.event_bus,
        "database": "postgres" if not settings.is_sqlite else "sqlite",
        "llm_evaluator": {
            "configured": provider_configured(),
            "provider": settings.supervisor_llm_provider,
            "model": settings.supervisor_model if provider_configured() else None,
        },
        "user": {"id": user.id, "email": user.email},
    }


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, session: DbSession) -> list[ApiKey]:
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars())


@router.post("/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    user: CurrentUser,
    session: DbSession,
    name: Annotated[str, Query(max_length=120)] = "default",
) -> ApiKeyCreated:
    plaintext, digest, prefix = generate_api_key()
    key = ApiKey(user_id=user.id, name=name, key_hash=digest, prefix=prefix)
    session.add(key)
    await session.flush()
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="api_key.created",
        target_type="api_key",
        target_id=key.id,
        detail={"name": name, "prefix": prefix},
    )
    await session.commit()

    # The plaintext exists only in this response; only the hash is stored.
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=plaintext)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, user: CurrentUser, session: DbSession) -> None:
    key = (
        await session.execute(
            select(ApiKey).where(ApiKey.id == key_id).where(ApiKey.user_id == user.id)
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    key.revoked_at = utcnow()
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="api_key.revoked",
        target_type="api_key",
        target_id=key.id,
    )
    await session.commit()


@router.get("/audit")
async def list_audit(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.user_id == user.id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "detail": r.detail,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
