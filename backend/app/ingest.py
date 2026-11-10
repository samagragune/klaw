"""Event ingestion: durable write, incremental rollups, live fan-out.

Ingestion is idempotent on ``(execution_id, client_event_id)`` so the SDK can
retry a batch after a network failure without corrupting counters.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.bus import get_bus, user_channel
from app.models import (
    Agent,
    AgentStatus,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    utcnow,
)
from app.pricing import estimate_cost
from app.schemas import EventIn

logger = logging.getLogger(__name__)


def _apply_event(execution: Execution, event: EventIn) -> None:
    """Fold one event into the execution's rollup columns and status."""
    payload = event.payload or {}
    execution.event_count += 1

    if event.event_type == E.TOOL_STARTED:
        execution.tool_call_count += 1
    elif event.event_type in (E.TOOL_FAILED, E.LLM_FAILED):
        execution.error_count += 1
    elif event.event_type == E.LLM_COMPLETED:
        prompt = int(payload.get("prompt_tokens") or 0)
        completion = int(payload.get("completion_tokens") or 0)
        execution.prompt_tokens += prompt
        execution.completion_tokens += completion
        execution.token_usage += prompt + completion
        cost = estimate_cost(payload.get("model"), prompt, completion)
        extra = dict(execution.extra or {})
        if cost is None:
            # Record that at least one call could not be priced, so the UI can
            # label the total as partial instead of implying completeness.
            unpriced = set(extra.get("unpriced_models", []))
            if payload.get("model"):
                unpriced.add(str(payload["model"]))
            extra["unpriced_models"] = sorted(unpriced)
            execution.extra = extra
            execution.cost_complete = False
        else:
            execution.estimated_cost = (execution.estimated_cost or 0.0) + cost
    elif event.event_type == E.EXECUTION_PAUSED:
        if execution.status == ExecutionStatus.RUNNING:
            execution.status = ExecutionStatus.PAUSED
    elif event.event_type == E.EXECUTION_RESUMED:
        if execution.status == ExecutionStatus.PAUSED:
            execution.status = ExecutionStatus.RUNNING
    elif event.event_type == E.EXECUTION_COMPLETED:
        execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = event.timestamp
        execution.output_summary = payload.get("output_summary")
    elif event.event_type == E.EXECUTION_FAILED:
        execution.status = ExecutionStatus.FAILED
        execution.completed_at = event.timestamp
        execution.error = payload.get("error")
    elif event.event_type == E.EXECUTION_CANCELLED:
        execution.status = ExecutionStatus.CANCELLED
        execution.completed_at = event.timestamp
        execution.error = payload.get("reason")


def _agent_status_for(execution: Execution, had_incident: bool) -> str:
    if execution.status == ExecutionStatus.RUNNING:
        return AgentStatus.WARNING if had_incident else AgentStatus.RUNNING
    if execution.status == ExecutionStatus.PAUSED:
        return AgentStatus.PAUSED
    if execution.status == ExecutionStatus.FAILED:
        return AgentStatus.FAILED
    return AgentStatus.IDLE


async def store_events(
    session: AsyncSession,
    *,
    execution: Execution,
    incoming: list[EventIn],
) -> tuple[list[ExecutionEvent], int]:
    """Persist new events, skipping ones already stored.

    Returns ``(stored_events, duplicate_count)``.
    """
    ids = [e.client_event_id for e in incoming]
    existing = set(
        (
            await session.execute(
                select(ExecutionEvent.client_event_id)
                .where(ExecutionEvent.execution_id == execution.id)
                .where(ExecutionEvent.client_event_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )

    stored: list[ExecutionEvent] = []
    duplicates = 0
    seen_in_batch: set[str] = set()

    for item in sorted(incoming, key=lambda e: e.seq):
        if item.client_event_id in existing or item.client_event_id in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(item.client_event_id)
        row = ExecutionEvent(
            execution_id=execution.id,
            user_id=execution.user_id,
            seq=item.seq,
            client_event_id=item.client_event_id,
            event_type=item.event_type,
            payload=item.payload or {},
            timestamp=item.timestamp,
        )
        session.add(row)
        stored.append(row)
        _apply_event(execution, item)

    return stored, duplicates


async def refresh_agent_state(
    session: AsyncSession, execution: Execution, *, had_incident: bool = False
) -> None:
    agent = (
        await session.execute(select(Agent).where(Agent.id == execution.agent_id))
    ).scalar_one_or_none()
    if agent is None:
        return
    agent.last_seen_at = utcnow()
    agent.status = _agent_status_for(execution, had_incident)


async def publish_execution_update(
    execution: Execution, stored: list[ExecutionEvent]
) -> None:
    """Fan out to dashboard subscribers. Best effort: never blocks ingestion."""
    bus = get_bus()
    payload = {
        "type": "execution.updated",
        "execution": {
            "id": execution.id,
            "agent_id": execution.agent_id,
            "status": execution.status,
            "task": execution.task,
            "event_count": execution.event_count,
            "tool_call_count": execution.tool_call_count,
            "error_count": execution.error_count,
            "token_usage": execution.token_usage,
            "estimated_cost": execution.estimated_cost,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": (
                execution.completed_at.isoformat() if execution.completed_at else None
            ),
        },
        "events": [
            {
                "id": e.id,
                "execution_id": execution.id,
                "seq": e.seq,
                "event_type": e.event_type,
                "timestamp": e.timestamp.isoformat(),
                "payload": e.payload,
            }
            for e in stored
        ],
    }
    try:
        await bus.publish(user_channel(execution.user_id), payload)
    except Exception:  # pragma: no cover - bus failure must not break ingest
        logger.exception("failed to publish execution update for %s", execution.id)
