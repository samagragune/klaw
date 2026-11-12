"""Human approval queue (dashboard side)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app import events as E
from app.bus import approval_channel, get_bus, user_channel
from app.ingest import CanonicalEvent, store_canonical
from app.models import ApprovalRequest, ApprovalStatus, Execution, utcnow
from app.routers.protocol import expire_if_due
from app.schemas import ApprovalDecisionIn, ApprovalOut
from app.security import CurrentUser, DbSession, record_audit

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    user: CurrentUser,
    session: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    execution_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ApprovalRequest]:
    # Expire anything overdue first so the queue never shows a stale request
    # as still answerable.
    pending = list(
        (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.user_id == user.id)
                .where(ApprovalRequest.status == ApprovalStatus.PENDING)
            )
        ).scalars()
    )
    if any([await expire_if_due(session, a) for a in pending]):
        await session.commit()

    query = select(ApprovalRequest).where(ApprovalRequest.user_id == user.id)
    if status_filter:
        query = query.where(ApprovalRequest.status == status_filter)
    if execution_id:
        query = query.where(ApprovalRequest.execution_id == execution_id)
    query = query.order_by(ApprovalRequest.created_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


@router.post("/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    approval_id: str, body: ApprovalDecisionIn, user: CurrentUser, session: DbSession
) -> ApprovalRequest:
    approval = (
        await session.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .where(ApprovalRequest.user_id == user.id)
        )
    ).scalar_one_or_none()
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    await expire_if_due(session, approval)
    if approval.status != ApprovalStatus.PENDING:
        await session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This request is already {approval.status}; the agent has moved on.",
        )

    approval.status = ApprovalStatus.APPROVED if body.approve else ApprovalStatus.DENIED
    approval.decided_at = utcnow()
    approval.decided_by = user.email
    approval.note = body.note

    execution = await session.get(Execution, approval.execution_id)
    if execution is not None:
        await store_canonical(
            session,
            execution=execution,
            incoming=[
                CanonicalEvent(
                    event_id=f"approval-resolved-{approval.id}",
                    event_type=E.APPROVAL_RESOLVED,
                    timestamp=utcnow(),
                    payload={
                        "approval_id": approval.id,
                        "status": approval.status,
                        "tool_name": approval.tool_name,
                        "decided_by": user.email,
                        "note": body.note,
                    },
                )
            ],
        )
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action=f"approval.{approval.status}",
        target_type="approval",
        target_id=approval.id,
        detail={"tool": approval.tool_name, "note": body.note},
    )
    await session.commit()

    bus = get_bus()
    await bus.publish(approval_channel(approval.id), {"status": approval.status})
    await bus.publish(
        user_channel(user.id),
        {"type": "approval.updated", "approval_id": approval.id, "status": approval.status},
    )
    return approval
