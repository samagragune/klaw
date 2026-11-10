"""Incident listing, status changes and optional LLM diagnosis."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.models import Agent, Execution, Incident, IncidentStatus
from app.schemas import IncidentDetail, IncidentOut, IncidentUpdate
from app.security import CurrentUser, DbSession, record_audit
from app.supervision.evaluator import diagnose_incident
from app.supervision.providers import EvaluatorUnavailable, provider_configured

router = APIRouter(prefix="/v1/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentDetail])
async def list_incidents(
    user: CurrentUser,
    session: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    severity: Annotated[str | None, Query()] = None,
    agent_id: Annotated[str | None, Query()] = None,
    execution_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[IncidentDetail]:
    query = (
        select(Incident, Agent.name, Execution.status, Execution.task)
        .join(Agent, Agent.id == Incident.agent_id)
        .join(Execution, Execution.id == Incident.execution_id)
        .where(Incident.user_id == user.id)
    )
    if status_filter:
        query = query.where(Incident.status == status_filter)
    if severity:
        query = query.where(Incident.severity == severity)
    if agent_id:
        query = query.where(Incident.agent_id == agent_id)
    if execution_id:
        query = query.where(Incident.execution_id == execution_id)
    query = query.order_by(Incident.created_at.desc()).limit(limit)

    rows = (await session.execute(query)).all()
    return [
        _as_detail(incident, agent_name, exec_status, exec_task)
        for incident, agent_name, exec_status, exec_task in rows
    ]


def _as_detail(
    incident: Incident, agent_name: str, execution_status: str, execution_task: str | None
) -> IncidentDetail:
    return IncidentDetail(
        **IncidentOut.model_validate(incident).model_dump(),
        agent_name=agent_name,
        execution_status=execution_status,
        execution_task=execution_task,
    )


async def _get_owned_incident(session, user, incident_id: str) -> Incident:
    incident = (
        await session.execute(
            select(Incident)
            .where(Incident.id == incident_id)
            .where(Incident.user_id == user.id)
        )
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: str, user: CurrentUser, session: DbSession
) -> IncidentDetail:
    incident = await _get_owned_incident(session, user, incident_id)
    agent = (
        await session.execute(select(Agent).where(Agent.id == incident.agent_id))
    ).scalar_one()
    execution = (
        await session.execute(select(Execution).where(Execution.id == incident.execution_id))
    ).scalar_one()
    return _as_detail(incident, agent.name, execution.status, execution.task)


@router.patch("/{incident_id}", response_model=IncidentOut)
async def update_incident(
    incident_id: str, body: IncidentUpdate, user: CurrentUser, session: DbSession
) -> Incident:
    incident = await _get_owned_incident(session, user, incident_id)
    incident.status = body.status
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="incident.status_changed",
        target_type="incident",
        target_id=incident.id,
        detail={"status": body.status},
    )
    await session.commit()
    return incident


@router.post("/{incident_id}/diagnose", response_model=IncidentOut)
async def diagnose(
    incident_id: str, user: CurrentUser, session: DbSession
) -> Incident:
    """Ask the configured evaluator to explain an incident.

    Deliberately explicit rather than automatic: it is the only path in the
    product that spends money, so it runs when a human asks for it.
    """
    if not provider_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No supervisor LLM provider is configured. Set SUPERVISOR_LLM_PROVIDER "
            "and SUPERVISOR_LLM_API_KEY to enable diagnosis. Deterministic detection "
            "is unaffected.",
        )
    incident = await _get_owned_incident(session, user, incident_id)
    execution = (
        await session.execute(select(Execution).where(Execution.id == incident.execution_id))
    ).scalar_one()

    try:
        await diagnose_incident(session, incident=incident, execution=execution)
    except EvaluatorUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:  # provider/network failure
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Evaluator call failed: {exc}"
        ) from exc

    await session.commit()
    return incident


@router.get("/stats/summary")
async def incident_summary(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Incident.severity, Incident.status).where(Incident.user_id == user.id)
        )
    ).all()
    by_severity: dict[str, int] = {}
    open_count = 0
    for severity, incident_status in rows:
        by_severity[severity] = by_severity.get(severity, 0) + 1
        if incident_status != IncidentStatus.RESOLVED:
            open_count += 1
    return {"total": len(rows), "open": open_count, "by_severity": by_severity}
