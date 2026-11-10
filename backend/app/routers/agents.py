"""Agent registration and listing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    Execution,
    ExecutionStatus,
    Incident,
    IncidentStatus,
    User,
    utcnow,
)
from app.schemas import AgentOut, AgentRegisterRequest, AgentSummary
from app.security import CurrentUser, DbSession, record_audit

router = APIRouter(prefix="/v1/agents", tags=["agents"])


async def get_owned_agent(session: AsyncSession, user: User, agent_id: str) -> Agent:
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == agent_id).where(Agent.user_id == user.id)
        )
    ).scalar_one_or_none()
    if agent is None:
        # 404 rather than 403: never confirm the existence of another
        # tenant's resource.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


@router.post("/register", response_model=AgentOut)
async def register_agent(
    body: AgentRegisterRequest, user: CurrentUser, session: DbSession
) -> Agent:
    """Idempotent registration: re-registering the same name updates metadata.

    The SDK calls this on every start-up, so it must not create duplicates.
    """
    existing = (
        await session.execute(
            select(Agent).where(Agent.user_id == user.id).where(Agent.name == body.name)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.framework = body.framework
        existing.sdk_version = body.sdk_version
        if body.description is not None:
            existing.description = body.description
        existing.last_seen_at = utcnow()
        await session.commit()
        return existing

    agent = Agent(
        user_id=user.id,
        name=body.name,
        framework=body.framework,
        description=body.description,
        sdk_version=body.sdk_version,
        last_seen_at=utcnow(),
    )
    session.add(agent)
    # Flush so the generated id exists before the audit row references it.
    await session.flush()
    await record_audit(
        session,
        user_id=user.id,
        actor="sdk",
        action="agent.registered",
        target_type="agent",
        target_id=agent.id,
        detail={"name": body.name, "framework": body.framework},
    )
    await session.commit()
    return agent


@router.get("", response_model=list[AgentSummary])
async def list_agents(user: CurrentUser, session: DbSession) -> list[AgentSummary]:
    agents = list(
        (
            await session.execute(
                select(Agent).where(Agent.user_id == user.id).order_by(Agent.created_at.desc())
            )
        ).scalars()
    )
    if not agents:
        return []

    # One grouped query per rollup rather than N+1 per agent.
    exec_rows = (
        await session.execute(
            select(
                Execution.agent_id,
                Execution.status,
                func.count(Execution.id),
                func.coalesce(func.sum(Execution.token_usage), 0),
                func.coalesce(func.sum(Execution.estimated_cost), 0.0),
                func.max(Execution.started_at),
            )
            .where(Execution.user_id == user.id)
            .group_by(Execution.agent_id, Execution.status)
        )
    ).all()

    incident_rows = (
        await session.execute(
            select(Incident.agent_id, func.count(Incident.id))
            .where(Incident.user_id == user.id)
            .where(Incident.status != IncidentStatus.RESOLVED)
            .group_by(Incident.agent_id)
        )
    ).all()
    open_incidents = {row[0]: row[1] for row in incident_rows}

    summaries: dict[str, AgentSummary] = {
        a.id: AgentSummary.model_validate(a) for a in agents
    }
    for agent_id, exec_status, count, tokens, cost, last_started in exec_rows:
        summary = summaries.get(agent_id)
        if summary is None:
            continue
        summary.execution_count += count
        summary.total_tokens += int(tokens or 0)
        summary.estimated_cost += float(cost or 0.0)
        if exec_status == ExecutionStatus.COMPLETED:
            summary.success_count += count
        elif exec_status in (ExecutionStatus.FAILED, ExecutionStatus.CANCELLED):
            summary.failure_count += count
        elif exec_status in (ExecutionStatus.RUNNING, ExecutionStatus.PAUSED):
            summary.running_count += count
        if last_started and (
            summary.last_activity_at is None or last_started > summary.last_activity_at
        ):
            summary.last_activity_at = last_started

    for summary in summaries.values():
        summary.open_incident_count = open_incidents.get(summary.id, 0)
        finished = summary.success_count + summary.failure_count
        # Undefined rather than 0% when nothing has finished yet.
        summary.success_rate = (
            round(summary.success_count / finished, 4) if finished else None
        )
        if summary.last_activity_at is None:
            summary.last_activity_at = summary.last_seen_at

    return sorted(
        summaries.values(),
        key=lambda s: (s.last_activity_at is None, s.last_activity_at),
        reverse=True,
    )


@router.get("/{agent_id}", response_model=AgentSummary)
async def get_agent(agent_id: str, user: CurrentUser, session: DbSession) -> AgentSummary:
    await get_owned_agent(session, user, agent_id)
    for summary in await list_agents(user, session):
        if summary.id == agent_id:
            return summary
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
