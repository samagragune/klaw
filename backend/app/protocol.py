"""Universal-protocol ingestion service.

Shared by ``POST /v1/events`` (asynchronous delivery) and
``POST /v1/decisions`` (a decision point, which stores its event first so the
trace shows exactly what the supervisor was asked about).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.control import compute_control_state, expire_pending_interventions
from app.ingest import (
    CanonicalEvent,
    close_dangling_tool_calls,
    publish_execution_update,
    refresh_agent_state,
    resolve_agent,
    resolve_session,
    store_canonical,
)
from app.models import Execution, ExecutionEvent, ExecutionStatus, Integration
from app.schemas import ProtocolEventIn, SessionControlOut
from app.security import Principal
from app.supervision.engine import evaluate_execution

#: Payload keys copied into the session context on first sight.
CONTEXT_KEYS = ("cwd", "model", "permission_mode", "host")


@dataclass
class SessionBatch:
    execution: Execution
    stored: list[ExecutionEvent] = field(default_factory=list)
    duplicates: int = 0
    completed_tasks: list[str] = field(default_factory=list)


async def resolve_integration(
    session: AsyncSession, principal: Principal, event: ProtocolEventIn
) -> Integration | None:
    """The integration an event belongs to, checked against the credential."""
    if principal.integration is not None:
        if event.integration_id and event.integration_id != principal.integration_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Event names a different integration than the key it was sent with",
            )
        if event.platform != principal.integration.platform:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"This key belongs to a {principal.integration.platform} integration; "
                f"event says {event.platform}",
            )
        return principal.integration
    if not event.integration_id:
        return None
    integration = await session.get(Integration, event.integration_id)
    if integration is None or integration.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    return integration


def to_canonical(event: ProtocolEventIn) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        payload=dict(event.payload),
        external_task_id=event.task_id,
        actor=event.execution_context.actor,
    )


async def ingest_protocol(
    session: AsyncSession, principal: Principal, events: list[ProtocolEventIn]
) -> list[SessionBatch]:
    """Store a batch, grouped per session. Commits."""
    groups: dict[tuple[str | None, str], list[ProtocolEventIn]] = {}
    integrations: dict[str | None, Integration | None] = {}
    for event in events:
        integration = await resolve_integration(session, principal, event)
        key = integration.id if integration else None
        integrations[key] = integration
        groups.setdefault((key, event.session_id), []).append(event)

    batches: list[SessionBatch] = []
    for (integration_id, session_id), group in groups.items():
        first = min(group, key=lambda e: e.timestamp)
        ctx = first.execution_context
        agent = await resolve_agent(
            session,
            user_id=principal.user_id,
            platform=first.platform,
            external_id=first.agent_id,
            integration_id=integration_id,
            name=next((e.agent_name for e in group if e.agent_name), None),
        )
        parent_id = None
        if ctx.parent_session_id:
            parent = (
                await session.execute(
                    select(Execution.id)
                    .where(Execution.user_id == principal.user_id)
                    .where(Execution.external_session_id == ctx.parent_session_id)
                )
            ).scalar_one_or_none()
            parent_id = parent
        context = {k: getattr(ctx, k) for k in CONTEXT_KEYS if getattr(ctx, k, None)}
        execution, _ = await resolve_session(
            session,
            user_id=principal.user_id,
            agent=agent,
            integration_id=integration_id,
            platform=first.platform,
            external_session_id=session_id,
            started_at=first.timestamp,
            context=context,
            parent_execution_id=parent_id,
        )
        stored, duplicates = await store_canonical(
            session, execution=execution, incoming=[to_canonical(e) for e in group]
        )
        if any(e.event_type in (E.TASK_COMPLETED, E.SESSION_ENDED) for e in stored):
            stored += await close_dangling_tool_calls(session, execution)
        batch = SessionBatch(execution=execution, stored=stored, duplicates=duplicates)
        batch.completed_tasks = [
            e.task_id for e in stored if e.event_type == E.TASK_COMPLETED and e.task_id
        ]
        batches.append(batch)

        incidents = await evaluate_execution(session, execution) if stored else []
        await refresh_agent_state(session, execution, had_incident=bool(incidents))
        if execution.status in ExecutionStatus.TERMINAL:
            await expire_pending_interventions(session, execution)

    await session.commit()
    for batch in batches:
        if batch.stored:
            await publish_execution_update(batch.execution, batch.stored)
    return batches


async def session_control(session: AsyncSession, execution: Execution) -> SessionControlOut:
    state = await compute_control_state(session, execution.id)
    return SessionControlOut(
        session_id=execution.external_session_id or execution.id,
        execution_id=execution.id,
        desired_state=state.desired_state,
        blocked_tools=state.blocked_tools,
        commands=state.commands,
    )
