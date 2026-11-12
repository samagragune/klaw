"""Event processor: resolve, store, fold, fan out.

One pipeline for every integration. Events arrive either in the universal
protocol (``POST /v1/events``, adapters) or in the legacy SDK vocabulary
(``POST /v1/ingest/events``); both end up here as canonical protocol events.

Properties this module guarantees:

* **Self-registering.** Agent, session and task are resolved - or created -
  from the identifiers on the event, so a stateless adapter (Claude Code runs
  one short process per hook) needs no setup round trip.
* **Idempotent** on ``(session, event_id)``: a retried delivery never
  double-counts.
* **Ordered.** Each stored event gets the next per-session sequence number;
  events in one batch are applied in timestamp order. The original timestamp
  is preserved for display.
* **Incremental.** Rollups on the session and task are updated as events are
  folded, so dashboards never scan the event table.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.bus import get_bus, user_channel
from app.models import (
    PLATFORM_CAPABILITIES,
    Agent,
    AgentStatus,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    Integration,
    Platform,
    Task,
    TaskStatus,
    utcnow,
)
from app.pricing import estimate_cost

logger = logging.getLogger(__name__)

MAX_TEXT = 4000

#: Platforms whose sessions span many tasks; finishing a task leaves the
#: session idle rather than over.
MULTI_TASK_PLATFORMS = {Platform.CLAUDE_CODE}


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalise before comparing."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clip(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


@dataclass
class CanonicalEvent:
    """An event after normalisation, ready to be folded and stored."""

    event_id: str
    event_type: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    external_task_id: str | None = None
    actor: str | None = None
    seq: int | None = None


def capabilities_of(execution: Execution, integration: Integration | None) -> set[str]:
    if integration is not None and integration.capabilities:
        return set(integration.capabilities)
    return set(PLATFORM_CAPABILITIES.get(execution.platform, ()))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

async def resolve_agent(
    session: AsyncSession,
    *,
    user_id: str,
    platform: str,
    external_id: str,
    integration_id: str | None,
    name: str | None = None,
    framework: str | None = None,
    description: str | None = None,
) -> Agent:
    query = (
        select(Agent)
        .where(Agent.user_id == user_id)
        .where(Agent.platform == platform)
        .where(Agent.external_id == external_id)
    )
    agent = (await session.execute(query)).scalar_one_or_none()
    if agent is None:
        agent = Agent(
            user_id=user_id,
            platform=platform,
            external_id=external_id,
            integration_id=integration_id,
            name=(name or external_id)[:200],
            framework=framework or platform,
            description=description,
            last_seen_at=utcnow(),
        )
        session.add(agent)
        try:
            # Committed on its own: resolution runs before any other work in
            # the request, so nothing else is pending, and a concurrent
            # request creating the same agent loses cleanly.
            await session.commit()
        except IntegrityError:
            await session.rollback()
            agent = (await session.execute(query)).scalar_one()
    else:
        if name and agent.name != name:
            agent.name = name[:200]
        if integration_id and not agent.integration_id:
            agent.integration_id = integration_id
        if description:
            agent.description = description
    agent.last_seen_at = utcnow()
    return agent


async def resolve_session(
    session: AsyncSession,
    *,
    user_id: str,
    agent: Agent,
    integration_id: str | None,
    platform: str,
    external_session_id: str,
    started_at: datetime,
    context: dict[str, Any] | None = None,
    parent_execution_id: str | None = None,
) -> tuple[Execution, bool]:
    """Return ``(execution, created)``."""
    query = (
        select(Execution)
        .where(Execution.user_id == user_id)
        .where(Execution.external_session_id == external_session_id)
    )
    query = (
        query.where(Execution.integration_id == integration_id)
        if integration_id
        else query.where(Execution.integration_id.is_(None))
    )
    execution = (await session.execute(query)).scalar_one_or_none()
    if execution is not None:
        if context:
            execution.context = {**(execution.context or {}), **context}
        return execution, False

    execution = Execution(
        user_id=user_id,
        agent_id=agent.id,
        integration_id=integration_id,
        platform=platform,
        external_session_id=external_session_id,
        parent_execution_id=parent_execution_id,
        status=(
            ExecutionStatus.IDLE
            if platform in MULTI_TASK_PLATFORMS
            else ExecutionStatus.RUNNING
        ),
        started_at=started_at,
        context=context or {},
    )
    session.add(execution)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        execution = (await session.execute(query)).scalar_one()
        return execution, False
    return execution, True


async def _current_task(session: AsyncSession, execution: Execution) -> Task | None:
    if not execution.current_task_id:
        return None
    return await session.get(Task, execution.current_task_id)


async def _task_by_external(
    session: AsyncSession, execution: Execution, external_task_id: str
) -> Task | None:
    return (
        await session.execute(
            select(Task)
            .where(Task.execution_id == execution.id)
            .where(Task.external_task_id == external_task_id)
        )
    ).scalar_one_or_none()


async def _open_task(
    session: AsyncSession,
    execution: Execution,
    *,
    objective: str | None,
    external_task_id: str | None,
    started_at: datetime,
) -> Task:
    previous = await _current_task(session, execution)
    if previous is not None and previous.status == TaskStatus.RUNNING:
        # A new objective arrived before the previous one reported completion:
        # interrupted, or the platform never told us. Say so; never claim done.
        previous.status = TaskStatus.ABANDONED
        previous.completed_at = started_at
    task = Task(
        user_id=execution.user_id,
        execution_id=execution.id,
        agent_id=execution.agent_id,
        external_task_id=external_task_id,
        objective=_clip(objective),
        started_at=started_at,
    )
    session.add(task)
    await session.flush()
    execution.current_task_id = task.id
    if objective and not execution.task:
        execution.task = _clip(objective, 2000)
    return task


async def _task_for_event(
    session: AsyncSession, execution: Execution, event: CanonicalEvent
) -> Task | None:
    """The task an activity event belongs to, opening one if needed."""
    if event.external_task_id:
        task = await _task_by_external(session, execution, event.external_task_id)
        if task is not None:
            return task
    task = await _current_task(session, execution)
    if task is not None and task.status == TaskStatus.RUNNING:
        return task
    if event.event_type in (
        E.TOOL_CALL_STARTED,
        E.MODEL_CALL,
        E.SUBAGENT_STARTED,
        E.AGENT_HANDOFF,
    ):
        # Activity with no announced objective: track it under an implicit
        # task rather than dropping the association.
        return await _open_task(
            session,
            execution,
            objective=None,
            external_task_id=event.external_task_id,
            started_at=event.timestamp,
        )
    return task


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------

def _close_task(task: Task | None, status: str, at: datetime) -> None:
    if task is not None and task.status == TaskStatus.RUNNING:
        task.status = status
        task.completed_at = at


async def apply_event(
    session: AsyncSession, execution: Execution, event: CanonicalEvent
) -> Task | None:
    """Fold one event into session + task state. Returns the task it belongs to."""
    p = event.payload
    t = event.event_type
    execution.event_count += 1

    if t in (E.TASK_CREATED, E.PROMPT_SUBMITTED):
        objective = p.get("objective") or p.get("prompt") or p.get("task")
        task = None
        if event.external_task_id:
            task = await _task_by_external(session, execution, event.external_task_id)
        if task is None:
            task = await _open_task(
                session,
                execution,
                objective=objective,
                external_task_id=event.external_task_id,
                started_at=event.timestamp,
            )
        elif objective and not task.objective:
            task.objective = _clip(objective)
        if execution.status in (ExecutionStatus.IDLE, ExecutionStatus.INACTIVE):
            execution.status = ExecutionStatus.RUNNING
        return task

    if t == E.SESSION_STARTED:
        objective = p.get("task") or p.get("objective")
        if objective and execution.platform not in MULTI_TASK_PLATFORMS:
            await _open_task(
                session,
                execution,
                objective=objective,
                external_task_id=event.external_task_id,
                started_at=event.timestamp,
            )
        if p.get("context"):
            execution.context = {**(execution.context or {}), **p["context"]}
        return await _current_task(session, execution)

    task = await _task_for_event(session, execution, event)

    if t == E.TOOL_CALL_STARTED:
        execution.tool_call_count += 1
        if task:
            task.tool_call_count += 1
        if execution.status in (ExecutionStatus.IDLE, ExecutionStatus.INACTIVE):
            execution.status = ExecutionStatus.RUNNING
    elif t == E.TOOL_CALL_FAILED:
        execution.error_count += 1
        if task:
            task.error_count += 1
    elif t == E.MODEL_CALL:
        if task:
            task.model_calls += 1
        if p.get("status") == "error":
            execution.error_count += 1
            if task:
                task.error_count += 1
        prompt = int(p.get("prompt_tokens") or 0)
        completion = int(p.get("completion_tokens") or 0)
        execution.prompt_tokens += prompt
        execution.completion_tokens += completion
        execution.token_usage += prompt + completion
        if prompt or completion:
            cost = estimate_cost(p.get("model"), prompt, completion)
            if cost is None:
                extra = dict(execution.extra or {})
                unpriced = set(extra.get("unpriced_models", []))
                if p.get("model"):
                    unpriced.add(str(p["model"]))
                extra["unpriced_models"] = sorted(unpriced)
                execution.extra = extra
                execution.cost_complete = False
            else:
                execution.estimated_cost = (execution.estimated_cost or 0.0) + cost
    elif t == E.AGENT_ERROR:
        execution.error_count += 1
        if task:
            task.error_count += 1
        execution.error = _clip(p.get("error") or p.get("message"), 2000)
        if p.get("fatal"):
            _close_task(task, TaskStatus.FAILED, event.timestamp)
            execution.status = ExecutionStatus.FAILED
            execution.completed_at = event.timestamp
    elif t == E.TASK_COMPLETED:
        status = p.get("status") or TaskStatus.COMPLETED
        output = p.get("output") or p.get("output_summary")
        if task:
            if output:
                task.output_summary = _clip(output)
            if task.status == TaskStatus.RUNNING:
                task.status = status if status in TaskStatus.TERMINAL else TaskStatus.COMPLETED
                task.completed_at = event.timestamp
        if output:
            execution.output_summary = _clip(output)
        if execution.platform in MULTI_TASK_PLATFORMS and execution.status == ExecutionStatus.RUNNING:
            execution.status = ExecutionStatus.IDLE
    elif t == E.TASK_UPDATED:
        if task and p.get("objective") and not task.objective:
            task.objective = _clip(p["objective"])
    elif t == E.AGENT_IDLE:
        if execution.status == ExecutionStatus.RUNNING:
            execution.status = ExecutionStatus.IDLE
    elif t == E.AGENT_STOPPED:
        _close_task(task, TaskStatus.CANCELLED, event.timestamp)
        if p.get("terminal") or execution.platform not in MULTI_TASK_PLATFORMS:
            execution.status = ExecutionStatus.CANCELLED
            execution.completed_at = event.timestamp
            execution.error = _clip(p.get("reason"), 2000)
        else:
            execution.status = ExecutionStatus.IDLE
    elif t == E.SESSION_PAUSED:
        if execution.status == ExecutionStatus.RUNNING:
            execution.status = ExecutionStatus.PAUSED
    elif t == E.SESSION_RESUMED:
        if execution.status == ExecutionStatus.PAUSED:
            execution.status = ExecutionStatus.RUNNING
    elif t == E.SESSION_ENDED:
        status = p.get("status") or E.END_COMPLETED
        output = p.get("output") or p.get("output_summary")
        if output:
            execution.output_summary = _clip(output)
            if task and not task.output_summary:
                task.output_summary = _clip(output)
        if status == E.END_FAILED:
            execution.status = ExecutionStatus.FAILED
            execution.error = _clip(p.get("error") or execution.error, 2000)
            _close_task(task, TaskStatus.FAILED, event.timestamp)
        elif status == E.END_CANCELLED:
            execution.status = ExecutionStatus.CANCELLED
            execution.error = _clip(p.get("reason") or execution.error, 2000)
            _close_task(task, TaskStatus.CANCELLED, event.timestamp)
        else:
            execution.status = ExecutionStatus.COMPLETED
            # A multi-task session ending with a turn still open means that
            # turn never finished; a single-task session ending cleanly means
            # its task did.
            _close_task(
                task,
                TaskStatus.ABANDONED
                if execution.platform in MULTI_TASK_PLATFORMS
                else TaskStatus.COMPLETED,
                event.timestamp,
            )
        execution.completed_at = event.timestamp
    return task


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

async def _next_seq(session: AsyncSession, execution: Execution) -> int:
    current = (
        await session.execute(
            select(func.max(ExecutionEvent.seq)).where(
                ExecutionEvent.execution_id == execution.id
            )
        )
    ).scalar_one()
    return 0 if current is None else current + 1


async def store_canonical(
    session: AsyncSession,
    *,
    execution: Execution,
    incoming: list[CanonicalEvent],
) -> tuple[list[ExecutionEvent], int]:
    """Persist new events, skipping duplicates. Returns ``(stored, duplicates)``."""
    if not incoming:
        return [], 0
    ids = [e.event_id for e in incoming]
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
    seen: set[str] = set()
    next_seq: int | None = None

    def order(e: CanonicalEvent):
        return (e.seq if e.seq is not None else 0, _aware(e.timestamp))

    for item in sorted(incoming, key=order):
        if item.event_id in existing or item.event_id in seen:
            duplicates += 1
            continue
        seen.add(item.event_id)
        if item.seq is not None:
            seq = item.seq
        else:
            if next_seq is None:
                next_seq = await _next_seq(session, execution)
            seq = next_seq
            next_seq += 1

        task = await apply_event(session, execution, item)
        row = ExecutionEvent(
            execution_id=execution.id,
            user_id=execution.user_id,
            seq=seq,
            client_event_id=item.event_id,
            event_type=item.event_type,
            payload=item.payload,
            timestamp=item.timestamp,
            task_id=task.id if task else None,
            actor=item.actor,
        )
        session.add(row)
        stored.append(row)
        previous = _aware(execution.last_event_at)
        if previous is None or _aware(item.timestamp) > previous:
            execution.last_event_at = item.timestamp

    if stored:
        execution.last_heartbeat_at = utcnow()
        await session.flush()
    return stored, duplicates


async def store_events(
    session: AsyncSession, *, execution: Execution, incoming: list[Any]
) -> tuple[list[ExecutionEvent], int]:
    """Legacy SDK batch: translate the dotted vocabulary, then store."""
    canonical: list[CanonicalEvent] = []
    dropped: list[str] = []
    for item in incoming:
        event_type, payload = E.normalise(item.event_type, dict(item.payload or {}))
        if event_type is None:
            dropped.append(item.client_event_id)
            continue
        canonical.append(
            CanonicalEvent(
                event_id=item.client_event_id,
                event_type=event_type,
                timestamp=item.timestamp,
                payload=_legacy_payload(event_type, payload),
                seq=item.seq,
            )
        )
    stored, duplicates = await store_canonical(
        session, execution=execution, incoming=canonical
    )
    return stored, duplicates


def _legacy_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Field renames between the legacy SDK and the protocol."""
    if event_type == E.SESSION_ENDED and "reason" in payload and "error" not in payload:
        payload = {**payload, "error": payload.get("reason")}
    return payload


# ---------------------------------------------------------------------------
# Agent state and fan-out
# ---------------------------------------------------------------------------

def _agent_status_for(execution: Execution, had_incident: bool) -> str:
    if execution.status == ExecutionStatus.RUNNING:
        return AgentStatus.WARNING if had_incident else AgentStatus.RUNNING
    if execution.status == ExecutionStatus.PAUSED:
        return AgentStatus.PAUSED
    if execution.status == ExecutionStatus.FAILED:
        return AgentStatus.FAILED
    return AgentStatus.IDLE


async def refresh_agent_state(
    session: AsyncSession, execution: Execution, *, had_incident: bool = False
) -> None:
    agent = await session.get(Agent, execution.agent_id)
    if agent is None:
        return
    agent.last_seen_at = utcnow()
    agent.status = _agent_status_for(execution, had_incident)


def execution_snapshot(execution: Execution) -> dict[str, Any]:
    return {
        "id": execution.id,
        "agent_id": execution.agent_id,
        "platform": execution.platform,
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
    }


async def publish_execution_update(
    execution: Execution, stored: list[ExecutionEvent]
) -> None:
    """Fan out to dashboard subscribers. Best effort: never blocks ingestion."""
    payload = {
        "type": "execution.updated",
        "execution": execution_snapshot(execution),
        "events": [
            {
                "id": e.id,
                "execution_id": execution.id,
                "seq": e.seq,
                "event_type": e.event_type,
                "timestamp": e.timestamp.isoformat(),
                "payload": e.payload,
                "task_id": e.task_id,
                "actor": e.actor,
            }
            for e in stored
        ],
    }
    try:
        await get_bus().publish(user_channel(execution.user_id), payload)
    except Exception:  # pragma: no cover - bus failure must not break ingest
        logger.exception("failed to publish execution update for %s", execution.id)


async def close_dangling_tool_calls(
    session: AsyncSession, execution: Execution
) -> list[ExecutionEvent]:
    """Tool calls that never reported an outcome by the time their task ended.

    Usually the platform's own permission system refused the call after the
    supervisor allowed it (Claude Code then sends neither PostToolUse nor
    PostToolUseFailure). Recorded as not run and marked as an inference, so
    the trace never shows a call as in flight forever.
    """
    rows = list(
        (
            await session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution.id)
                .where(
                    ExecutionEvent.event_type.in_(
                        (
                            E.TOOL_CALL_STARTED,
                            E.TOOL_CALL_COMPLETED,
                            E.TOOL_CALL_FAILED,
                            E.TOOL_CALL_BLOCKED,
                        )
                    )
                )
            )
        ).scalars()
    )
    started: dict[str, ExecutionEvent] = {}
    finished: set[str] = set()
    for row in rows:
        call_id = (row.payload or {}).get("tool_call_id")
        if not call_id:
            continue
        if row.event_type == E.TOOL_CALL_STARTED:
            started[call_id] = row
        else:
            finished.add(call_id)
    dangling = [row for call_id, row in started.items() if call_id not in finished]
    if not dangling:
        return []
    now = utcnow()
    stored, _ = await store_canonical(
        session,
        execution=execution,
        incoming=[
            CanonicalEvent(
                event_id=f"unreported-{row.id}",
                event_type=E.TOOL_CALL_BLOCKED,
                timestamp=now,
                payload={
                    "tool_name": (row.payload or {}).get("tool_name"),
                    "tool_call_id": (row.payload or {}).get("tool_call_id"),
                    "by": "platform",
                    "inferred": True,
                    "reason": "No result was reported before the task ended; the "
                    "platform most likely refused it (for example its own "
                    "permission rules).",
                },
            )
            for row in dangling
        ],
    )
    return stored
