"""Supervision engine: policy resolution, incident lifecycle, autopilot.

Deterministic detection and enforcement live here. The LLM evaluator is
strictly additive - it enriches an incident with a diagnosis, and is never
required for an incident to be raised. Hard limits are enforced by this
module, not by a model, which is the constraint the specification calls out.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import get_bus, user_channel
from app.control import create_intervention, notify_control_change
from app.models import (
    Execution,
    ExecutionEvent,
    Incident,
    IncidentStatus,
    Intervention,
    InterventionStatus,
    Policy,
    SupervisionMode,
    utcnow,
)
from app.schemas import PolicyConfiguration
from app.supervision.detectors import Detection, run_detectors

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = PolicyConfiguration().model_dump()
DEFAULT_MODE = SupervisionMode.ASSIST

#: Cap on how many events one evaluation pass loads. Executions longer than
#: this are already pathological and will have tripped a limit detector.
MAX_EVENTS_PER_EVALUATION = 5000


async def effective_policy(
    session: AsyncSession, *, user_id: str, agent_id: str
) -> tuple[str, dict[str, Any], Policy | None]:
    """Resolve agent policy, else the user default, else built-in defaults."""
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
    if policy is None:
        return DEFAULT_MODE, dict(DEFAULT_CONFIG), None
    config = {**DEFAULT_CONFIG, **(policy.configuration or {})}
    return policy.supervision_mode, config, policy


async def _load_events(session: AsyncSession, execution_id: str) -> list[ExecutionEvent]:
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id)
        .order_by(ExecutionEvent.seq)
        .limit(MAX_EVENTS_PER_EVALUATION)
    )
    return list(result.scalars())


async def _upsert_incident(
    session: AsyncSession,
    *,
    execution: Execution,
    detection: Detection,
    mode: str,
) -> tuple[Incident, bool]:
    """Create or refresh the single open incident per (execution, category)."""
    existing = (
        await session.execute(
            select(Incident)
            .where(Incident.execution_id == execution.id)
            .where(Incident.category == detection.category)
        )
    ).scalar_one_or_none()

    recommended = (
        None if mode == SupervisionMode.OBSERVE else detection.recommended_action
    )

    if existing is not None:
        changed = (
            existing.evidence != detection.evidence
            or existing.severity != detection.severity
        )
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


async def _maybe_autopilot(
    session: AsyncSession,
    *,
    execution: Execution,
    incident: Incident,
    detection: Detection,
    mode: str,
    config: dict[str, Any],
) -> Intervention | None:
    """Enforce a limit automatically, only within explicit authorisation."""
    if mode != SupervisionMode.AUTOPILOT:
        return None
    action = detection.suggested_intervention
    if not action:
        return None
    if detection.category in (config.get("require_approval_for") or []):
        incident.action_taken = (
            f"Autopilot held back: '{detection.category}' requires human approval "
            "under the active policy."
        )
        return None
    if action not in (config.get("permitted_interventions") or []):
        incident.action_taken = (
            f"Autopilot held back: '{action}' is not in the policy's permitted "
            "interventions."
        )
        return None

    # Do not stack commands for an incident that already triggered one.
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
            reason=f"Autopilot: {detection.title}",
            requested_by="supervisor",
            incident_id=incident.id,
        )
    except ValueError:
        # Execution finished between detection and enforcement.
        return None

    incident.action_taken = (
        f"Autopilot requested '{action}'. Awaiting runtime acknowledgement."
    )
    return intervention


async def evaluate_execution(
    session: AsyncSession, execution: Execution
) -> list[Incident]:
    """Run every deterministic detector and reconcile incidents.

    The caller is responsible for committing. Returns the incidents that were
    created or materially changed by this pass.
    """
    mode, config, _ = await effective_policy(
        session, user_id=execution.user_id, agent_id=execution.agent_id
    )
    events = await _load_events(session, execution.id)
    detections = run_detectors(execution, events, config)

    touched: list[Incident] = []
    new_interventions: list[tuple[Intervention, Execution]] = []

    for detection in detections:
        incident, changed = await _upsert_incident(
            session, execution=execution, detection=detection, mode=mode
        )
        intervention = await _maybe_autopilot(
            session,
            execution=execution,
            incident=incident,
            detection=detection,
            mode=mode,
            config=config,
        )
        if intervention is not None:
            new_interventions.append((intervention, execution))
        if changed or intervention is not None:
            touched.append(incident)

    if touched or new_interventions:
        await session.flush()

    for incident in touched:
        await _publish_incident(execution, incident)
    for intervention, ex in new_interventions:
        await notify_control_change(ex, intervention)

    return touched


async def _publish_incident(execution: Execution, incident: Incident) -> None:
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
