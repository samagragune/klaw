"""Dashboard read models: platforms, tasks, decisions, live activity, costs."""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.models import (
    Agent,
    ApprovalRequest,
    ApprovalStatus,
    EvaluatorCall,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    Incident,
    IncidentStatus,
    Integration,
    SupervisorDecision,
    Task,
    utcnow,
)
from app.routers.integrations import PLATFORM_LABELS
from app.schemas import EventOut, SupervisorDecisionOut, TaskOut
from app.security import CurrentUser, DbSession
from app.supervision import risk
from app.supervision.reliability import WEIGHTS, reliability_scores

router = APIRouter(prefix="/v1", tags=["analytics"])


@router.get("/metrics/platforms")
async def platform_overview(user: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    """Per-platform grouping for the overview."""
    agents = dict(
        (
            await session.execute(
                select(Agent.platform, func.count(Agent.id))
                .where(Agent.user_id == user.id)
                .group_by(Agent.platform)
            )
        ).all()
    )
    live = dict(
        (
            await session.execute(
                select(Execution.platform, func.count(Execution.id))
                .where(Execution.user_id == user.id)
                .where(Execution.status.in_(tuple(ExecutionStatus.LIVE)))
                .group_by(Execution.platform)
            )
        ).all()
    )
    sessions = dict(
        (
            await session.execute(
                select(Execution.platform, func.count(Execution.id))
                .where(Execution.user_id == user.id)
                .group_by(Execution.platform)
            )
        ).all()
    )
    incidents = dict(
        (
            await session.execute(
                select(Execution.platform, func.count(Incident.id))
                .join(Execution, Execution.id == Incident.execution_id)
                .where(Incident.user_id == user.id)
                .where(Incident.status != IncidentStatus.RESOLVED)
                .group_by(Execution.platform)
            )
        ).all()
    )
    integrations = dict(
        (
            await session.execute(
                select(Integration.platform, func.count(Integration.id))
                .where(Integration.user_id == user.id)
                .where(Integration.revoked_at.is_(None))
                .group_by(Integration.platform)
            )
        ).all()
    )
    platforms = sorted(set(agents) | set(integrations) | set(sessions))
    return [
        {
            "platform": p,
            "label": PLATFORM_LABELS.get(p, p),
            "integrations": integrations.get(p, 0),
            "agents": agents.get(p, 0),
            "live_sessions": live.get(p, 0),
            "sessions": sessions.get(p, 0),
            "open_incidents": incidents.get(p, 0),
        }
        for p in platforms
    ]


@router.get("/metrics/reliability")
async def reliability(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    scores = await reliability_scores(session, user.id)
    names = dict(
        (await session.execute(select(Agent.id, Agent.name).where(Agent.user_id == user.id))).all()
    )
    platforms = dict(
        (
            await session.execute(
                select(Agent.id, Agent.platform).where(Agent.user_id == user.id)
            )
        ).all()
    )
    return {
        "formula": "100 x (0.5 x task_success + 0.3 x clean_rate + 0.2 x tool_success)",
        "weights": WEIGHTS,
        "agents": [
            {"agent_id": a, "name": names.get(a), "platform": platforms.get(a), **v}
            for a, v in sorted(
                scores.items(), key=lambda kv: (kv[1]["score"] is None, kv[1]["score"] or 0)
            )
        ],
    }


@router.get("/metrics/evaluation-costs")
async def evaluation_costs(
    user: CurrentUser,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                EvaluatorCall.purpose,
                EvaluatorCall.model,
                func.count(EvaluatorCall.id),
                func.count(EvaluatorCall.id).filter(EvaluatorCall.cached.is_(True)),
                func.count(EvaluatorCall.id).filter(EvaluatorCall.ok.is_(False)),
                func.coalesce(func.sum(EvaluatorCall.prompt_tokens), 0),
                func.coalesce(func.sum(EvaluatorCall.completion_tokens), 0),
                func.sum(EvaluatorCall.estimated_cost),
                func.count(EvaluatorCall.id).filter(
                    EvaluatorCall.estimated_cost.is_(None) & EvaluatorCall.cached.is_(False)
                ),
                func.avg(EvaluatorCall.latency_ms).filter(EvaluatorCall.cached.is_(False)),
            )
            .where(EvaluatorCall.user_id == user.id)
            .where(EvaluatorCall.created_at >= since)
            .group_by(EvaluatorCall.purpose, EvaluatorCall.model)
        )
    ).all()
    breakdown = [
        {
            "purpose": r[0],
            "model": r[1],
            "calls": r[2],
            "cached": r[3],
            "failed": r[4],
            "prompt_tokens": int(r[5]),
            "completion_tokens": int(r[6]),
            "estimated_cost": float(r[7]) if r[7] is not None else None,
            "unpriced_calls": r[8],
            "avg_latency_ms": round(float(r[9]), 1) if r[9] is not None else None,
        }
        for r in rows
    ]
    return {
        "days": days,
        "calls": sum(b["calls"] for b in breakdown),
        "cached": sum(b["cached"] for b in breakdown),
        "tokens": sum(b["prompt_tokens"] + b["completion_tokens"] for b in breakdown),
        "estimated_cost": sum(b["estimated_cost"] or 0 for b in breakdown),
        "cost_complete": all(b["unpriced_calls"] == 0 for b in breakdown),
        "breakdown": breakdown,
    }


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    user: CurrentUser,
    session: DbSession,
    execution_id: str | None = None,
    agent_id: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Task]:
    query = select(Task).where(Task.user_id == user.id)
    if execution_id:
        query = query.where(Task.execution_id == execution_id)
    if agent_id:
        query = query.where(Task.agent_id == agent_id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    query = query.order_by(Task.started_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


@router.get("/decisions", response_model=list[SupervisorDecisionOut])
async def list_decisions(
    user: CurrentUser,
    session: DbSession,
    execution_id: str | None = None,
    include_allow: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SupervisorDecision]:
    query = select(SupervisorDecision).where(SupervisorDecision.user_id == user.id)
    if execution_id:
        query = query.where(SupervisorDecision.execution_id == execution_id)
    if not include_allow:
        query = query.where(
            (SupervisorDecision.action != "ALLOW")
            | SupervisorDecision.intended_action.is_not(None)
        )
    query = query.order_by(SupervisorDecision.created_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


@router.get("/activity")
async def live_activity(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> list[dict[str, Any]]:
    """Most recent events across every session, newest first."""
    rows = (
        await session.execute(
            select(ExecutionEvent, Execution.platform, Execution.agent_id, Agent.name)
            .join(Execution, Execution.id == ExecutionEvent.execution_id)
            .join(Agent, Agent.id == Execution.agent_id)
            .where(ExecutionEvent.user_id == user.id)
            .order_by(ExecutionEvent.timestamp.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            **EventOut.model_validate(event).model_dump(mode="json"),
            "execution_id": event.execution_id,
            "platform": platform,
            "agent_id": agent_id,
            "agent_name": name,
        }
        for event, platform, agent_id, name in rows
    ]


@router.get("/metrics/supervision")
async def supervision_summary(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Counts for the overview: decisions by action, pending approvals."""
    by_action = dict(
        (
            await session.execute(
                select(SupervisorDecision.action, func.count(SupervisorDecision.id))
                .where(SupervisorDecision.user_id == user.id)
                .group_by(SupervisorDecision.action)
            )
        ).all()
    )
    pending = (
        await session.execute(
            select(func.count(ApprovalRequest.id))
            .where(ApprovalRequest.user_id == user.id)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING)
            .where(ApprovalRequest.expires_at > utcnow())
        )
    ).scalar_one()
    tasks = dict(
        (
            await session.execute(
                select(Task.status, func.count(Task.id))
                .where(Task.user_id == user.id)
                .group_by(Task.status)
            )
        ).all()
    )
    return {"decisions": by_action, "pending_approvals": pending, "tasks": tasks}


@router.get("/risk-rules")
async def risk_rules(user: CurrentUser) -> list[dict[str, str]]:
    return risk.catalogue()
