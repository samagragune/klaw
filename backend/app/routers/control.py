"""Ingestion and the execution control plane.

The two SDK-facing endpoints that make control real:

``POST /v1/ingest/events``
    Durably stores trace events, updates rollups, runs the deterministic
    detectors, and returns the authoritative control state in the same
    response - so a busy agent learns it must pause without any extra call.

``GET /v1/executions/{id}/control``
    A long poll. It returns immediately when there is something to say, and
    otherwise parks on the event bus until a command is issued or the timeout
    elapses. That gives push latency for an idle agent without a second
    protocol.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.bus import control_channel, get_bus, user_channel
from app.config import get_settings
from app.control import (
    apply_acknowledgement,
    compute_control_state,
    create_intervention,
    expire_pending_interventions,
    notify_control_change,
)
from app.ingest import publish_execution_update, refresh_agent_state, store_events
from app.models import ExecutionStatus, Intervention
from app.routers.executions import get_owned_execution
from app.schemas import (
    ControlStateOut,
    EventBatchIn,
    IngestResult,
    InterventionAck,
    InterventionCreate,
    InterventionOut,
)
from app.security import CurrentUser, DbSession, record_audit
from app.supervision.engine import evaluate_execution
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["control"])


@router.post("/ingest/events", response_model=IngestResult)
async def ingest_events(
    body: EventBatchIn, user: CurrentUser, session: DbSession
) -> IngestResult:
    execution = await get_owned_execution(session, user, body.execution_id)

    stored, duplicates = await store_events(
        session, execution=execution, incoming=body.events
    )

    incidents = await evaluate_execution(session, execution)
    await refresh_agent_state(session, execution, had_incident=bool(incidents))

    if execution.status in ExecutionStatus.TERMINAL:
        await expire_pending_interventions(session, execution)

    state = await compute_control_state(session, execution.id)
    await session.commit()

    if stored:
        await publish_execution_update(execution, stored)

    return IngestResult(accepted=len(stored), duplicates=duplicates, control=state)


@router.get("/executions/{execution_id}/control", response_model=ControlStateOut)
async def poll_control(
    execution_id: str,
    user: CurrentUser,
    session: DbSession,
    wait: Annotated[float, Query(ge=0, le=60)] = 0,
) -> ControlStateOut:
    execution = await get_owned_execution(session, user, execution_id)
    state = await compute_control_state(session, execution.id)
    await session.commit()

    if state.commands or state.desired_state != "running" or wait <= 0:
        return state

    if execution.status in ExecutionStatus.TERMINAL:
        return state

    timeout = min(wait, get_settings().control_poll_timeout_seconds)
    message = await get_bus().wait_for_message(control_channel(execution_id), timeout)
    if message is None:
        return state

    # Re-read: the command was written by a different request on a different
    # session, so this session's snapshot predates it.
    await session.rollback()
    execution = await get_owned_execution(session, user, execution_id)
    state = await compute_control_state(session, execution.id)
    await session.commit()
    return state


@router.post(
    "/executions/{execution_id}/interventions",
    response_model=InterventionOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_intervention(
    execution_id: str,
    body: InterventionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Intervention:
    """Issue a control command.

    This records intent. The command is not reported as applied until the
    runtime acknowledges it through ``/v1/interventions/ack``.
    """
    execution = await get_owned_execution(session, user, execution_id)

    if body.action == "block_tool" and not body.parameters.get("tool_name"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "block_tool requires parameters.tool_name",
        )

    try:
        intervention = await create_intervention(
            session,
            execution=execution,
            action=body.action,
            reason=body.reason,
            requested_by="user",
            parameters=body.parameters,
            incident_id=body.incident_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action=f"intervention.{body.action}",
        target_type="execution",
        target_id=execution.id,
        detail={"intervention_id": intervention.id, "reason": body.reason},
    )
    await session.commit()
    await notify_control_change(execution, intervention)
    return intervention


@router.get(
    "/executions/{execution_id}/interventions", response_model=list[InterventionOut]
)
async def list_interventions(
    execution_id: str, user: CurrentUser, session: DbSession
) -> list[Intervention]:
    await get_owned_execution(session, user, execution_id)
    result = await session.execute(
        select(Intervention)
        .where(Intervention.execution_id == execution_id)
        .order_by(Intervention.created_at)
    )
    return list(result.scalars())


@router.post("/interventions/ack", response_model=InterventionOut)
async def acknowledge_intervention(
    body: InterventionAck, user: CurrentUser, session: DbSession
) -> Intervention:
    """Runtime confirmation that a command was received or enforced.

    Only the owner of the execution may acknowledge, and only through a
    credential that already passed authentication - a runtime cannot mark
    another tenant's command applied.
    """
    intervention = (
        await session.execute(
            select(Intervention)
            .where(Intervention.id == body.intervention_id)
            .where(Intervention.user_id == user.id)
        )
    ).scalar_one_or_none()
    if intervention is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intervention not found")

    await apply_acknowledgement(
        session, intervention=intervention, status=body.status, detail=body.detail
    )
    await record_audit(
        session,
        user_id=user.id,
        actor="runtime",
        action=f"intervention.{body.status}",
        target_type="intervention",
        target_id=intervention.id,
        detail={"action": intervention.action, **body.detail},
    )
    await session.commit()

    await get_bus().publish(
        user_channel(user.id),
        {
            "type": "intervention.updated",
            "execution_id": intervention.execution_id,
            "intervention": {
                "id": intervention.id,
                "action": intervention.action,
                "status": intervention.status,
                "applied_at": intervention.applied_at.isoformat()
                if intervention.applied_at
                else None,
                "runtime_result": intervention.runtime_result,
            },
        },
    )
    return intervention
