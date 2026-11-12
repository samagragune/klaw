"""Supervision engine: policy resolution, incident lifecycle, asynchronous control.

Deterministic detection and enforcement live here; the LLM evaluator only ever
adds a diagnosis. Two enforcement paths exist, and which one applies depends
on what the platform can do:

* **Asynchronous control** (runtime-control platforms - the custom SDK, the
  OpenAI Agents SDK adapter): in CONTROL mode the engine issues a pause or
  cancel command, which the runtime applies at its next checkpoint and
  confirms. Unchanged from before the rework.
* **Decision-point control** (Claude Code): nothing can be pushed into a
  running Claude Code turn. Instead the next decision point - the next tool
  call - consults the same findings and blocks or warns there
  (``supervision/decisions.py``). The incident records which path applies.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import get_bus, user_channel
from app.control import create_intervention, notify_control_change
from app.models import (
    Capability,
    Execution,
    ExecutionEvent,
    Incident,
    IncidentStatus,
    Integration,
    Intervention,
    InterventionStatus,
    Platform,
    Policy,
    SupervisionMode,
    Task,
    utcnow,
)
from app.schemas import PolicyConfiguration
from app.supervision.detectors import Detection, run_detectors

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = PolicyConfiguration().model_dump()
DEFAULT_MODE = SupervisionMode.RECOMMEND

#: Defaults that differ by platform. A Claude Code task is a human-scale unit
#: of work (minutes, hundreds of tool calls), and hooks report no token usage.
PLATFORM_DEFAULTS: dict[str, dict[str, Any]] = {
    Platform.CLAUDE_CODE: {
        "max_duration_seconds": 3600,
        "max_tool_calls": 300,
        "max_tokens": None,
        "max_cost_usd": None,
        "stall_seconds": None,
    },
}

#: Cap on how many events one evaluation pass loads.
MAX_EVENTS_PER_EVALUATION = 5000


async def effective_policy(
    session: AsyncSession, *, user_id: str, agent_id: str, platform: str | None = None
) -> tuple[str, dict[str, Any], Policy | None]:
    """Agent policy, else the user default, else built-in (platform) defaults.

    A policy stores only the fields it sets; everything else falls through to
    the platform default and then the global default.
    """
    rows = list(
        (
            await session.execute(
                select(Policy)
                .where(Policy.user_id == user_id)
                .where((Policy.agent_id == agent_id) | (Policy.agent_id.is_(None)))
            )
        ).scalars()
    )
    agent_policy = next((p for p in rows if p.agent_id == agent_id), None)
    default_policy = next((p for p in rows if p.agent_id is None), None)
    policy = agent_policy or default_policy

    base = {**DEFAULT_CONFIG, **PLATFORM_DEFAULTS.get(platform or "", {})}
    if policy is None:
        return DEFAULT_MODE, base, None
    config = {**base, **(policy.configuration or {})}
    return SupervisionMode.normalise(policy.supervision_mode), config, policy


async def policy_for_execution(
    session: AsyncSession, execution: Execution
) -> tuple[str, dict[str, Any], Policy | None]:
    return await effective_policy(
        session,
        user_id=execution.user_id,
        agent_id=execution.agent_id,
        platform=execution.platform,
    )


async def execution_capabilities(session: AsyncSession, execution: Execution) -> set[str]:
    from app.ingest import capabilities_of

    integration = (
        await session.get(Integration, execution.integration_id)
        if execution.integration_id
        else None
    )
    return capabilities_of(execution, integration)


async def load_events(session: AsyncSession, execution_id: str) -> list[ExecutionEvent]:
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id)
        .order_by(ExecutionEvent.seq)
        .limit(MAX_EVENTS_PER_EVALUATION)
    )
    return list(result.scalars())


async def _upsert_incident(
    session: AsyncSession, *, execution: Execution, detection: Detection, mode: str
) -> tuple[Incident, bool]:
    """One incident per (session, task, category); refreshed as evidence grows."""
    query = (
        select(Incident)
        .where(Incident.execution_id == execution.id)
        .where(Incident.category == detection.category)
    )
    query = (
        query.where(Incident.task_id == detection.task_id)
        if detection.task_id
        else query.where(Incident.task_id.is_(None))
    )
    existing = (await session.execute(query)).scalar_one_or_none()
    recommended = None if mode == SupervisionMode.OBSERVE else detection.recommended_action

    if existing is not None:
        changed = existing.evidence != detection.evidence or existing.severity != detection.severity
        existing.evidence = detection.evidence
        existing.severity = detection.severity
        existing.title = detection.title
        existing.description = detection.description
        existing.recommended_action = recommended
        existing.updated_at = utcnow()
        return existing, changed

    incident = Incident(
        execution_id=execution.id,
        agent_id=execution.agent_id,
        user_id=execution.user_id,
        task_id=detection.task_id,
        severity=detection.severity,
        category=detection.category,
        title=detection.title,
        description=detection.description,
        evidence=detection.evidence,
        source="deterministic",
        recommended_action=recommended,
        status=IncidentStatus.OPEN,
    )
    session.add(incident)
    await session.flush()
    return incident, True


async def _maybe_control(
    session: AsyncSession,
    *,
    execution: Execution,
    incident: Incident,
    detection: Detection,
    mode: str,
    config: dict[str, Any],
    capabilities: set[str],
) -> Intervention | None:
    """Enforce a limit asynchronously - only with authority and capability."""
    if mode != SupervisionMode.CONTROL:
        return None
    action = detection.suggested_intervention
    if not action:
        return None
    if detection.category in (config.get("require_approval_for") or []):
        incident.action_taken = (
            f"Held back: '{detection.category}' requires human approval under the "
            "active policy."
        )
        return None
    if action not in (config.get("permitted_interventions") or []):
        incident.action_taken = (
            f"Held back: '{action}' is not in the policy's permitted interventions."
        )
        return None
    if Capability.RUNTIME_CONTROL not in capabilities:
        # Nothing can be pushed into this platform mid-turn. Say what will
        # happen instead, rather than recording a command that cannot apply.
        incident.action_taken = (
            f"'{action}' cannot be applied asynchronously on {execution.platform}. "
            + (
                "The supervisor will act at the next tool call instead."
                if Capability.BLOCK_TOOL in capabilities
                else "Recorded for a human to act on."
            )
        )
        return None

    already = (
        await session.execute(
            select(Intervention)
            .where(Intervention.incident_id == incident.id)
            .where(
                Intervention.status.in_(
                    (
                        InterventionStatus.PENDING,
                        InterventionStatus.ACKNOWLEDGED,
                        InterventionStatus.APPLIED,
                    )
                )
            )
        )
    ).first()
    if already is not None:
        return None

    try:
        intervention = await create_intervention(
            session,
            execution=execution,
            action=action,
            reason=f"Supervisor: {detection.title}",
            requested_by="supervisor",
            incident_id=incident.id,
        )
    except ValueError:
        return None
    incident.action_taken = f"Supervisor requested '{action}'. Awaiting runtime confirmation."
    return intervention


async def evaluate_execution(session: AsyncSession, execution: Execution) -> list[Incident]:
    """Run every deterministic detector and reconcile incidents. Caller commits."""
    mode, config, _ = await policy_for_execution(session, execution)
    capabilities = await execution_capabilities(session, execution)
    events = await load_events(session, execution.id)
    task = await session.get(Task, execution.current_task_id) if execution.current_task_id else None
    detections = run_detectors(execution, events, config, task)

    touched: list[Incident] = []
    new_interventions: list[Intervention] = []
    for detection in detections:
        incident, changed = await _upsert_incident(
            session, execution=execution, detection=detection, mode=mode
        )
        intervention = await _maybe_control(
            session,
            execution=execution,
            incident=incident,
            detection=detection,
            mode=mode,
            config=config,
            capabilities=capabilities,
        )
        if intervention is not None:
            new_interventions.append(intervention)
        if changed or intervention is not None:
            touched.append(incident)

    if touched or new_interventions:
        await session.flush()
    for incident in touched:
        await publish_incident(execution, incident)
    for intervention in new_interventions:
        await notify_control_change(execution, intervention)
    return touched


async def publish_incident(execution: Execution, incident: Incident) -> None:
    try:
        await get_bus().publish(
            user_channel(execution.user_id),
            {
                "type": "incident.updated",
                "incident": {
                    "id": incident.id,
                    "execution_id": incident.execution_id,
                    "agent_id": incident.agent_id,
                    "severity": incident.severity,
                    "category": incident.category,
                    "title": incident.title,
                    "status": incident.status,
                    "created_at": incident.created_at.isoformat()
                    if incident.created_at
                    else None,
                },
            },
        )
    except Exception:  # pragma: no cover - bus failure must not break detection
        logger.exception("failed to publish incident %s", incident.id)


# Backwards-compatible name used by output_review.
_publish_incident = publish_incident
