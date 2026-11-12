"""Execution lifecycle and trace reads."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import get_bus, user_channel
from app.models import (
    Agent,
    AgentStatus,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    Incident,
    Intervention,
    User,
    utcnow,
)
from app.routers.agents import get_owned_agent
from app.models import ApprovalRequest, SupervisorDecision, Task, TaskStatus
from app.supervision.engine import execution_capabilities
from app.schemas import (
    ApprovalOut,
    SupervisorDecisionOut,
    TaskOut,
    EventOut,
    ExecutionDetail,
    ExecutionOut,
    ExecutionStartRequest,
    IncidentOut,
    InterventionOut,
)
from app.security import CurrentUser, DbSession
from app.supervision.output_review import review_output
from app.supervision.providers import EvaluatorUnavailable, provider_configured

router = APIRouter(prefix="/v1/executions", tags=["executions"])


async def get_owned_execution(
    session: AsyncSession, user: User, execution_id: str
) -> Execution:
    execution = (
        await session.execute(
            select(Execution)
            .where(Execution.id == execution_id)
            .where(Execution.user_id == user.id)
        )
    ).scalar_one_or_none()
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    return execution


@router.post("", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED)
async def start_execution(
    body: ExecutionStartRequest, user: CurrentUser, session: DbSession
) -> Execution:
    agent = await get_owned_agent(session, user, body.agent_id)

    if body.execution_id:
        existing = (
            await session.execute(
                select(Execution)
                .where(Execution.id == body.execution_id)
                .where(Execution.user_id == user.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Retry of a start request the SDK already made.
            return existing

    execution = Execution(
        user_id=user.id,
        agent_id=agent.id,
        task=body.task,
        status=ExecutionStatus.RUNNING,
        extra=body.metadata or {},
        platform=agent.platform,
        integration_id=agent.integration_id,
    )
    if body.execution_id:
        execution.id = body.execution_id

    session.add(execution)
    agent.status = AgentStatus.RUNNING
    agent.last_seen_at = utcnow()
    await session.commit()

    await get_bus().publish(
        user_channel(user.id),
        {
            "type": "execution.started",
            "execution": {
                "id": execution.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "status": execution.status,
                "task": execution.task,
                "started_at": execution.started_at.isoformat(),
            },
        },
    )
    return execution


@router.get("", response_model=list[ExecutionOut])
async def list_executions(
    user: CurrentUser,
    session: DbSession,
    agent_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    active: Annotated[bool, Query()] = False,
    platform: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Execution]:
    query = select(Execution).where(Execution.user_id == user.id)
    if platform:
        query = query.where(Execution.platform == platform)
    if agent_id:
        query = query.where(Execution.agent_id == agent_id)
    if status_filter:
        query = query.where(Execution.status == status_filter)
    if active:
        query = query.where(Execution.status.in_(tuple(ExecutionStatus.LIVE)))
    query = query.order_by(Execution.started_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


@router.get("/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: str,
    user: CurrentUser,
    session: DbSession,
    event_limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> ExecutionDetail:
    execution = await get_owned_execution(session, user, execution_id)
    agent = (
        await session.execute(select(Agent).where(Agent.id == execution.agent_id))
    ).scalar_one()

    events = list(
        (
            await session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.seq)
                .limit(event_limit)
            )
        ).scalars()
    )
    incidents = list(
        (
            await session.execute(
                select(Incident)
                .where(Incident.execution_id == execution_id)
                .order_by(Incident.created_at)
            )
        ).scalars()
    )
    interventions = list(
        (
            await session.execute(
                select(Intervention)
                .where(Intervention.execution_id == execution_id)
                .order_by(Intervention.created_at)
            )
        ).scalars()
    )

    return ExecutionDetail(
        **ExecutionOut.model_validate(execution).model_dump(),
        agent_name=agent.name,
        events=[EventOut.model_validate(e) for e in events],
        incidents=[IncidentOut.model_validate(i) for i in incidents],
        interventions=[InterventionOut.model_validate(i) for i in interventions],
        output_evaluation=(execution.extra or {}).get("output_evaluation"),
        tasks=[
            TaskOut.model_validate(t)
            for t in (
                await session.execute(
                    select(Task).where(Task.execution_id == execution_id).order_by(Task.started_at)
                )
            ).scalars()
        ],
        decisions=[
            SupervisorDecisionOut.model_validate(d)
            for d in (
                await session.execute(
                    select(SupervisorDecision)
                    .where(SupervisorDecision.execution_id == execution_id)
                    .order_by(SupervisorDecision.created_at)
                    .limit(500)
                )
            ).scalars()
        ],
        approvals=[
            ApprovalOut.model_validate(a)
            for a in (
                await session.execute(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.execution_id == execution_id)
                    .order_by(ApprovalRequest.created_at)
                )
            ).scalars()
        ],
        capabilities=sorted(await execution_capabilities(session, execution)),
    )


@router.get("/{execution_id}/events", response_model=list[EventOut])
async def list_events(
    execution_id: str,
    user: CurrentUser,
    session: DbSession,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[ExecutionEvent]:
    await get_owned_execution(session, user, execution_id)
    query = select(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id)
    if after_seq is not None:
        query = query.where(ExecutionEvent.seq > after_seq)
    query = query.order_by(ExecutionEvent.seq).limit(limit)
    return list((await session.execute(query)).scalars())


@router.post("/{execution_id}/evaluate-output")
async def evaluate_execution_output(
    execution_id: str, user: CurrentUser, session: DbSession
) -> dict:
    """Ask the supervisor LLM whether a finished run met its objective.

    Raises a ``poor_output`` incident (source ``llm``) only on a confident
    failing verdict. The verdict itself is stored on the execution either way.
    """
    if not provider_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No supervisor LLM provider is configured; output evaluation needs one.",
        )
    execution = await get_owned_execution(session, user, execution_id)
    # What must be finished is the work being judged: the session for
    # single-task platforms, the latest task for multi-task ones (a Claude
    # Code session idles between turns and may never "end").
    task = await session.get(Task, execution.current_task_id) if execution.current_task_id else None
    finished = (
        task.status in TaskStatus.TERMINAL
        if task is not None
        else execution.status in ExecutionStatus.TERMINAL
    )
    if not finished:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The work is still running; evaluate it once it ends."
        )
    try:
        verdict, incident = await review_output(session, execution=execution)
    except EvaluatorUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:  # provider/network failure
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Evaluator call failed: {exc}"
        ) from exc
    await session.commit()
    return {
        "verdict": verdict,
        "incident_id": incident.id if incident is not None else None,
    }
