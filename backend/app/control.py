"""Execution control plane.

The honest-control contract, in one place:

* The backend never *stops* anything. It records an intended state and hands
  commands to the SDK runtime, which is the only thing that can actually
  suspend or abort the agent.
* ``desired_state`` is derived from the ordered history of control commands
  that have not failed or expired, so a pause followed by a resume resolves
  correctly no matter how the SDK polls.
* An intervention only becomes ``applied`` when the runtime posts an
  acknowledgement. Nothing in the API or UI reports success before that.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import control_channel, get_bus, user_channel
from app.models import (
    Execution,
    ExecutionStatus,
    Intervention,
    InterventionAction,
    InterventionStatus,
    utcnow,
)
from app.schemas import CommandOut, ControlStateOut

logger = logging.getLogger(__name__)

#: Statuses that still influence the runtime's desired state.
LIVE_STATUSES = (
    InterventionStatus.PENDING,
    InterventionStatus.ACKNOWLEDGED,
    InterventionStatus.APPLIED,
)

#: Statuses that still require an acknowledgement from the runtime.
UNACKED_STATUSES = (InterventionStatus.PENDING, InterventionStatus.ACKNOWLEDGED)


async def load_interventions(
    session: AsyncSession, execution_id: str, statuses: tuple[str, ...]
) -> list[Intervention]:
    result = await session.execute(
        select(Intervention)
        .where(Intervention.execution_id == execution_id)
        .where(Intervention.status.in_(statuses))
        .order_by(Intervention.created_at, Intervention.id)
    )
    return list(result.scalars())


async def compute_control_state(
    session: AsyncSession, execution_id: str
) -> ControlStateOut:
    """Fold the command history into the state the runtime should be in."""
    live = await load_interventions(session, execution_id, LIVE_STATUSES)

    desired = "running"
    blocked_tools: list[str] = []
    for iv in live:
        if iv.action == InterventionAction.CANCEL:
            desired = "cancelled"
            break
        if iv.action == InterventionAction.PAUSE:
            desired = "paused"
        elif iv.action == InterventionAction.RESUME:
            desired = "running"
        elif iv.action == InterventionAction.BLOCK_TOOL:
            tool = iv.parameters.get("tool_name")
            if tool and tool not in blocked_tools:
                blocked_tools.append(tool)

    commands = [
        CommandOut(
            intervention_id=iv.id,
            action=iv.action,
            reason=iv.reason,
            parameters=iv.parameters or {},
        )
        for iv in live
        if iv.status in UNACKED_STATUSES
    ]

    # Mark delivery attempts. delivered_at is "we put it on the wire", which
    # is deliberately weaker than acknowledged_at ("the runtime has it").
    now = utcnow()
    for iv in live:
        if iv.status == InterventionStatus.PENDING and iv.delivered_at is None:
            iv.delivered_at = now

    return ControlStateOut(
        execution_id=execution_id,
        desired_state=desired,  # type: ignore[arg-type]
        commands=commands,
        blocked_tools=blocked_tools,
    )


async def create_intervention(
    session: AsyncSession,
    *,
    execution: Execution,
    action: str,
    reason: str | None,
    requested_by: str,
    parameters: dict[str, Any] | None = None,
    incident_id: str | None = None,
) -> Intervention:
    """Record a control request and wake any waiting runtime poll.

    Raises ``ValueError`` when the execution can no longer be controlled.
    """
    if execution.status in ExecutionStatus.TERMINAL:
        raise ValueError(
            f"execution is already {execution.status}; it can no longer be controlled"
        )

    intervention = Intervention(
        execution_id=execution.id,
        user_id=execution.user_id,
        incident_id=incident_id,
        action=action,
        reason=reason,
        requested_by=requested_by,
        parameters=parameters or {},
        status=InterventionStatus.PENDING,
    )
    session.add(intervention)
    await session.flush()
    return intervention


async def notify_control_change(execution: Execution, intervention: Intervention) -> None:
    """Push the command to a waiting long-poll and to the dashboard."""
    bus = get_bus()
    await bus.publish(
        control_channel(execution.id),
        {"type": "control.updated", "execution_id": execution.id},
    )
    await bus.publish(
        user_channel(execution.user_id),
        {
            "type": "intervention.created",
            "execution_id": execution.id,
            "agent_id": execution.agent_id,
            "intervention": {
                "id": intervention.id,
                "action": intervention.action,
                "status": intervention.status,
                "reason": intervention.reason,
                "requested_by": intervention.requested_by,
            },
        },
    )


async def apply_acknowledgement(
    session: AsyncSession,
    *,
    intervention: Intervention,
    status: str,
    detail: dict[str, Any],
) -> Intervention:
    """Record what the runtime reported back about a command."""
    now = utcnow()
    if status == "acknowledged":
        # Received by the runtime but not yet enforced at a checkpoint.
        if intervention.status == InterventionStatus.PENDING:
            intervention.status = InterventionStatus.ACKNOWLEDGED
            intervention.acknowledged_at = now
    elif status == "applied":
        intervention.status = InterventionStatus.APPLIED
        intervention.acknowledged_at = intervention.acknowledged_at or now
        intervention.applied_at = now
    elif status == "failed":
        intervention.status = InterventionStatus.FAILED
        intervention.acknowledged_at = intervention.acknowledged_at or now
    intervention.runtime_result = {
        "reported_status": status,
        "reported_at": now.isoformat(),
        **detail,
    }
    return intervention


async def expire_pending_interventions(
    session: AsyncSession, execution: Execution
) -> list[Intervention]:
    """Close out commands that can no longer be applied.

    Called when an execution reaches a terminal state. A cancel command whose
    execution actually ended as cancelled counts as applied - the runtime did
    what it was told, even if the ack and the terminal event raced.
    """
    pending = await load_interventions(session, execution.id, UNACKED_STATUSES)
    now = utcnow()
    for iv in pending:
        if (
            iv.action == InterventionAction.CANCEL
            and execution.status == ExecutionStatus.CANCELLED
        ):
            iv.status = InterventionStatus.APPLIED
            iv.applied_at = iv.applied_at or now
            iv.acknowledged_at = iv.acknowledged_at or now
            iv.runtime_result = {
                "reported_status": "applied",
                "reported_at": now.isoformat(),
                "note": "execution terminated as cancelled",
            }
        else:
            iv.status = InterventionStatus.EXPIRED
            iv.runtime_result = {
                "reported_status": "expired",
                "reported_at": now.isoformat(),
                "note": f"execution finished as {execution.status} before the command was applied",
            }
    return pending
