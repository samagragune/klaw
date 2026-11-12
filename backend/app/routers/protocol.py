"""Adapter-facing endpoints of the universal protocol.

``POST /v1/events``      asynchronous events, batched. Returns each touched
                         session's control state so an adapter learns about a
                         stop or block without an extra request.
``POST /v1/decisions``   a decision point. Stores the pending event, decides,
                         and returns one action. Must be fast: Claude Code is
                         waiting on it before running a tool.
``GET  /v1/approvals/{id}/wait``  long-poll until a human decides or the
                         approval expires.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app import events as E
from app.bus import approval_channel, get_bus
from app.config import get_settings
from app.models import ApprovalRequest, ApprovalStatus, Execution, ExecutionEvent, utcnow
from app.protocol import ingest_protocol, resolve_integration, session_control
from app.schemas import (
    ApprovalOut,
    ApprovalTicket,
    DecisionOut,
    DecisionRequestIn,
    ProtocolBatchIn,
    ProtocolIngestOut,
)
from app.security import CurrentPrincipal, DbSession, Principal, record_audit
from app.supervision.decisions import decide
from app.supervision.engine import policy_for_execution
from app.supervision.output_review import schedule_output_review
from app.supervision.providers import provider_configured

router = APIRouter(prefix="/v1", tags=["protocol"])


async def owned_execution(session, principal: Principal, execution_id: str) -> Execution:
    """An execution the credential may act on (integration keys: their own)."""
    query = (
        select(Execution)
        .where(Execution.id == execution_id)
        .where(Execution.user_id == principal.user_id)
    )
    if principal.integration is not None:
        query = query.where(Execution.integration_id == principal.integration_id)
    execution = (await session.execute(query)).scalar_one_or_none()
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return execution


async def _maybe_review(session, execution: Execution, task_ids: list[str]) -> None:
    if not task_ids or not provider_configured():
        return
    _, config, _ = await policy_for_execution(session, execution)
    if not config.get("evaluate_output_on_completion"):
        return
    for task_id in task_ids:
        schedule_output_review(execution.id, task_id)


@router.post("/events", response_model=ProtocolIngestOut)
async def ingest_events(
    body: ProtocolBatchIn, principal: CurrentPrincipal, session: DbSession
) -> ProtocolIngestOut:
    batches = await ingest_protocol(session, principal, body.events)
    out = []
    for batch in batches:
        out.append(await session_control(session, batch.execution))
        await _maybe_review(session, batch.execution, batch.completed_tasks)
    await session.commit()
    return ProtocolIngestOut(
        accepted=sum(len(b.stored) for b in batches),
        duplicates=sum(b.duplicates for b in batches),
        sessions=out,
    )


@router.post("/decisions", response_model=DecisionOut)
async def request_decision(
    body: DecisionRequestIn, principal: CurrentPrincipal, session: DbSession
) -> DecisionOut:
    integration = await resolve_integration(session, principal, body.event)
    [batch] = await ingest_protocol(session, principal, [body.event])
    execution = batch.execution
    event = (
        await session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == execution.id)
            .where(ExecutionEvent.client_event_id == body.event.event_id)
        )
    ).scalar_one_or_none()

    decision, approval = await decide(
        session,
        execution=execution,
        integration=integration,
        event=event,
        event_type=body.event.event_type,
        payload=body.event.payload,
        already_redirected=body.already_redirected,
    )
    if decision.action != "ALLOW":
        await record_audit(
            session,
            user_id=execution.user_id,
            actor="supervisor",
            action=f"decision.{decision.action.lower()}",
            target_type="execution",
            target_id=execution.id,
            detail={"rule": decision.rule, "reason": decision.reason, "tool": decision.tool_name},
        )
    await session.commit()
    if body.event.event_type == E.TASK_COMPLETED and decision.action == "ALLOW":
        await _maybe_review(session, execution, batch.completed_tasks)

    ticket = None
    if approval is not None:
        ticket = ApprovalTicket(
            id=approval.id,
            expires_at=approval.expires_at,
            on_timeout=approval.on_timeout,  # type: ignore[arg-type]
            wait_url=f"/v1/approvals/{approval.id}/wait",
        )
    return DecisionOut(
        decision_id=decision.id,
        execution_id=execution.id,
        action=decision.action,  # type: ignore[arg-type]
        reason=decision.reason,
        feedback=decision.feedback,
        rule=decision.rule,
        approval=ticket,
        intended_action=decision.intended_action,
        held_back_reason=decision.held_back_reason,
    )


async def expire_if_due(session, approval: ApprovalRequest) -> bool:
    if approval.status == ApprovalStatus.PENDING and approval.expires_at <= utcnow():
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = utcnow()
        approval.decided_by = "timeout"
        approval.note = f"No decision before the deadline; adapter applies '{approval.on_timeout}'."
        return True
    return False


@router.get("/approvals/{approval_id}/wait", response_model=ApprovalOut)
async def wait_for_approval(
    approval_id: str,
    principal: CurrentPrincipal,
    session: DbSession,
    wait: Annotated[float, Query(ge=0, le=60)] = 25,
) -> ApprovalRequest:
    async def load() -> ApprovalRequest:
        approval = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id)
                .where(ApprovalRequest.user_id == principal.user_id)
            )
        ).scalar_one_or_none()
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
        return approval

    approval = await load()
    await owned_execution(session, principal, approval.execution_id)
    if await expire_if_due(session, approval):
        await session.commit()
    if approval.status != ApprovalStatus.PENDING or wait <= 0:
        return approval

    remaining = (approval.expires_at - utcnow()).total_seconds()
    timeout = max(0.0, min(wait, remaining, get_settings().control_poll_timeout_seconds))
    await get_bus().wait_for_message(approval_channel(approval.id), timeout)
    await session.rollback()
    approval = await load()
    if await expire_if_due(session, approval):
        await session.commit()
    return approval
