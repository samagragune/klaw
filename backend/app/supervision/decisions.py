"""Intervention engine for decision points.

A decision point is a moment where an adapter can still change what the agent
does: a tool call about to run (Claude Code ``PreToolUse``, an Agents SDK tool
guardrail), or an agent about to finish (Claude Code ``Stop``). The adapter
sends the pending event and gets back one action.

Rules, in order - the first that decides wins:

1. **Operator commands** - a cancel on the session -> STOP; a tool blocked
   from the dashboard -> BLOCK.
2. **Policy deny list** (control mode) -> BLOCK.
3. **Risk rules** - destructive or credential-touching operations.
4. **Loop guard** - the same call already returned the same result
   ``repeated_tool_call_threshold`` times in this task, or already failed
   ``repeated_error_threshold`` times.
5. **Approval list** (control mode) -> REQUEST_APPROVAL.
6. **Completion review** (opt-in) - at task completion, the output is
   reviewed and, if it misses the task, the agent is sent back once with the
   gaps (REDIRECT).
7. Otherwise ALLOW.

Then two filters, both recorded on the decision rather than silently applied:

* **Mode** - OBSERVE never changes behaviour; RECOMMEND turns changes of
  course into WARN (feedback the agent sees, nothing refused).
* **Capability** - an action the integration cannot perform is degraded
  (REQUEST_APPROVAL -> BLOCK -> WARN -> ALLOW) and the decision says what was
  intended and why it was held back.

Every decision is stored (``SupervisorDecision``) and non-ALLOW decisions are
also written into the session trace.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.bus import get_bus, user_channel
from app.control import compute_control_state
from app.ingest import CanonicalEvent, capabilities_of, store_canonical
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    DecisionAction as A,
    Execution,
    ExecutionEvent,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Integration,
    SupervisionMode,
    SupervisorDecision,
    Task,
    utcnow,
)
from app.supervision import risk
from app.supervision.detectors import loop_groups
from app.supervision.engine import load_events, policy_for_execution, publish_incident

logger = logging.getLogger(__name__)

#: Order in which an unsupported action is softened.
DEGRADE = {
    A.REQUEST_APPROVAL: [A.BLOCK, A.WARN, A.ALLOW],
    A.BLOCK: [A.WARN, A.ALLOW],
    A.STOP: [A.BLOCK, A.WARN, A.ALLOW],
    A.PAUSE: [A.WARN, A.ALLOW],
    A.REDIRECT: [A.WARN, A.ALLOW],
    A.RETRY: [A.WARN, A.ALLOW],
    A.WARN: [A.ALLOW],
}

#: Actions that change the agent's course; RECOMMEND turns them into WARN.
COURSE_CHANGING = {A.BLOCK, A.REQUEST_APPROVAL, A.STOP, A.PAUSE, A.REDIRECT, A.RETRY}


@dataclass
class Verdict:
    action: str
    rule: str
    reason: str
    feedback: str | None = None
    #: Incident to raise alongside the decision.
    incident: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None


def _supported(action: str, capabilities: set[str]) -> bool:
    needed = A.REQUIRES.get(action)
    return needed is None or needed in capabilities


def _apply_mode(verdict: Verdict, mode: str) -> tuple[str, str | None]:
    """Returns ``(action, held_back_reason)``."""
    if verdict.action == A.ALLOW:
        return A.ALLOW, None
    if verdict.rule.startswith(("operator.", "platform.")):
        # Modes bound the supervisor's own authority. An operator's explicit
        # command, or a platform asking for a human, is not the supervisor
        # changing the agent's course.
        return verdict.action, None
    if mode == SupervisionMode.OBSERVE:
        return A.ALLOW, "Observe mode: recorded, behaviour unchanged."
    if mode == SupervisionMode.RECOMMEND and verdict.action in COURSE_CHANGING:
        return A.WARN, "Recommend mode: the agent is told, nothing is refused."
    return verdict.action, None


def _apply_capability(action: str, capabilities: set[str]) -> tuple[str, str | None]:
    if _supported(action, capabilities):
        return action, None
    for fallback in DEGRADE.get(action, [A.ALLOW]):
        if _supported(fallback, capabilities):
            return fallback, f"This platform cannot {action.lower().replace('_', ' ')}."
    return A.ALLOW, f"This platform cannot {action.lower().replace('_', ' ')}."


def _preview(tool_input: Any) -> dict[str, Any]:
    if isinstance(tool_input, dict):
        out: dict[str, Any] = {}
        for key, value in list(tool_input.items())[:12]:
            text = value if isinstance(value, str) else repr(value)
            out[key] = text if len(text) <= 600 else text[:600] + "..."
        return out
    if tool_input is None:
        return {}
    text = str(tool_input)
    return {"input": text if len(text) <= 600 else text[:600] + "..."}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

async def _operator_rule(
    session: AsyncSession, execution: Execution, tool_name: str | None
) -> Verdict | None:
    state = await compute_control_state(session, execution.id)
    if state.desired_state == "cancelled":
        reason = next(
            (c.reason for c in state.commands if c.action == "cancel" and c.reason),
            "Stopped by an operator",
        )
        return Verdict(A.STOP, "operator.stop", reason, feedback=reason)
    if tool_name and tool_name in state.blocked_tools:
        return Verdict(
            A.BLOCK,
            "operator.block_tool",
            f"'{tool_name}' was blocked by an operator.",
            feedback=f"The operator has blocked {tool_name}. Do not use it.",
        )
    return None


def _policy_lists(config: dict[str, Any], tool_name: str | None, mode: str) -> Verdict | None:
    if not tool_name or mode != SupervisionMode.CONTROL:
        return None
    if tool_name in (config.get("blocked_tools") or []):
        return Verdict(
            A.BLOCK,
            "policy.blocked_tool",
            f"'{tool_name}' is on the policy's blocked list.",
            feedback=f"{tool_name} is not permitted by policy. Use another approach.",
        )
    return None


def _approval_list(config: dict[str, Any], tool_name: str | None, mode: str) -> Verdict | None:
    if tool_name and mode == SupervisionMode.CONTROL and tool_name in (
        config.get("approval_required_tools") or []
    ):
        return Verdict(
            A.REQUEST_APPROVAL,
            "policy.approval_required",
            f"'{tool_name}' requires human approval under the active policy.",
        )
    return None


def _risk_rule(config: dict[str, Any], tool_name: str | None, tool_input: Any) -> Verdict | None:
    if not config.get("risk_rules_enabled", True):
        return None
    findings = risk.assess(tool_name, tool_input)
    if not findings:
        return None
    top = findings[0]
    severe = top.severity in ("critical", "high")
    wanted = {
        "request_approval": A.REQUEST_APPROVAL,
        "block": A.BLOCK,
        "warn": A.WARN,
    }[config.get("risk_action", "request_approval")]
    action = wanted if severe else A.WARN
    reason = f"{top.description} (rule {top.rule}): `{top.matched}`"
    if top.category == "runaway":
        return Verdict(
            wanted if severe else A.WARN,
            f"risk.{top.rule}",
            reason,
            feedback=(
                "Supervisor: this loop has no bound. If the condition never becomes "
                "true it runs forever (and keeps the session alive). Add a maximum "
                "number of attempts or a timeout, or report the failure instead."
            ),
            incident={
                "category": "risky_operation",
                "severity": IncidentSeverity.HIGH,
                "title": "Unbounded retry loop inside a single command",
                "description": f"{tool_name} was about to run a loop with no exit bound.",
                "evidence": {"tool_name": tool_name, "findings": [f.__dict__ for f in findings]},
            },
        )
    return Verdict(
        action,
        f"risk.{top.rule}",
        reason,
        feedback=(
            f"Supervisor flagged this as risky: {top.description.lower()}. "
            + ("It needs approval before it can run." if action == A.REQUEST_APPROVAL else
               "It was refused." if action == A.BLOCK else
               "Proceed only if it is really necessary.")
        ),
        incident={
            "category": "risky_operation",
            "severity": IncidentSeverity.HIGH if severe else IncidentSeverity.MEDIUM,
            "title": f"Risky operation: {top.description}",
            "description": f"{tool_name} was about to run something matching {top.rule}.",
            "evidence": {
                "tool_name": tool_name,
                "findings": [f.__dict__ for f in findings],
            },
        },
    )


def _loop_rule(
    config: dict[str, Any],
    task_events: list[ExecutionEvent],
    tool_name: str | None,
    args_fp: str | None,
) -> Verdict | None:
    if not tool_name or not args_fp:
        return None
    threshold = config.get("repeated_tool_call_threshold")
    error_threshold = config.get("repeated_error_threshold")
    same_args = {
        key: calls
        for key, calls in loop_groups(task_events).items()
        if key[0] == tool_name and key[1] == args_fp
    }
    for (_, _, result_fp), calls in same_args.items():
        failures = result_fp.startswith("error:")
        limit = error_threshold if failures else threshold
        if not limit or len(calls) < limit:
            continue
        what = "failed" if failures else "returned the same result"
        action = A.BLOCK if config.get("loop_action", "block") == "block" else A.WARN
        feedback = (
            f"This exact {tool_name} call has already {what} {len(calls)} times in "
            "this task. Running it again will not help. Try a different approach, or "
            "stop and explain what is blocking you."
        )
        return Verdict(
            action,
            "loop.repeated_failure" if failures else "loop.repeated_call",
            f"Identical {tool_name} call already {what} {len(calls)} times "
            f"(threshold {limit}).",
            feedback=feedback,
        )
    return None


async def _completion_rule(
    session: AsyncSession,
    execution: Execution,
    task: Task | None,
    config: dict[str, Any],
    payload: dict[str, Any],
    already_redirected: bool,
) -> Verdict | None:
    if not config.get("review_output_before_completion") or task is None:
        return None
    if already_redirected or task.redirects >= int(config.get("max_redirects", 1)):
        # Never send an agent back twice for the same reason; that is how
        # correction turns into a loop.
        return None
    output = payload.get("output") or payload.get("output_summary")
    if not output:
        return None
    from app.supervision.output_review import review_task_output

    try:
        verdict, _ = await review_task_output(
            session, execution=execution, task=task, output=output, raise_incident=False
        )
    except Exception as exc:  # evaluator down: never block completion on it
        logger.warning("completion review unavailable: %s", exc)
        return None
    if verdict.get("satisfies_objective") is not False or str(
        verdict.get("confidence", "")
    ).lower() not in ("high", "medium"):
        return None
    gaps = [str(g) for g in (verdict.get("gaps") or [])][:6]
    return Verdict(
        A.REDIRECT,
        "review.incomplete_output",
        "The output does not yet meet the task: " + ("; ".join(gaps) or "see review"),
        feedback=(
            "Before finishing, address these gaps the supervisor found in your answer:\n"
            + "\n".join(f"- {g}" for g in gaps)
            + "\nIf a gap cannot be closed, say so explicitly instead."
        ),
        incident={
            "category": "poor_output",
            "severity": IncidentSeverity.MEDIUM,
            "title": "Output did not meet the task at completion",
            "description": str(verdict.get("reasoning") or ""),
            "evidence": {"gaps": gaps, "evaluator": verdict.get("evaluator")},
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def decide(
    session: AsyncSession,
    *,
    execution: Execution,
    integration: Integration | None,
    event: ExecutionEvent | None,
    event_type: str,
    payload: dict[str, Any],
    already_redirected: bool = False,
) -> tuple[SupervisorDecision, ApprovalRequest | None]:
    """Decide on one pending event. Caller commits."""
    started = time.perf_counter()
    mode, config, _ = await policy_for_execution(session, execution)
    capabilities = capabilities_of(execution, integration)
    task = await session.get(Task, execution.current_task_id) if execution.current_task_id else None

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    verdict: Verdict | None = None

    verdict = await _operator_rule(session, execution, tool_name)
    if verdict is None and event_type == E.TOOL_CALL_STARTED:
        all_events = await load_events(session, execution.id)
        task_events = [
            e
            for e in all_events
            if (task is None or e.task_id == task.id) and (event is None or e.id != event.id)
        ]
        verdict = (
            _policy_lists(config, tool_name, mode)
            or _risk_rule(config, tool_name, tool_input)
            or _loop_rule(config, task_events, tool_name, payload.get("args_fingerprint"))
            or _approval_list(config, tool_name, mode)
        )
        if verdict is None and tool_input is None and config.get("risk_rules_enabled", True):
            verdict = Verdict(
                A.ALLOW,
                "default.allow_unscreened",
                "Allowed. The adapter did not send the tool input, so risk rules "
                "could not screen it.",
            )
    elif verdict is None and event_type == E.APPROVAL_REQUESTED:
        if config.get("forward_permission_requests"):
            verdict = Verdict(
                A.REQUEST_APPROVAL,
                "platform.permission_prompt",
                f"{execution.platform} asked for permission to use '{tool_name}'.",
            )
    elif verdict is None and event_type == E.TASK_COMPLETED:
        verdict = await _completion_rule(
            session, execution, task, config, payload, already_redirected
        )
    verdict = verdict or Verdict(A.ALLOW, "default.allow", "No rule applies.")

    action, held_mode = _apply_mode(verdict, mode)
    action, held_capability = _apply_capability(action, capabilities)
    held_back = held_mode or held_capability
    intended = verdict.action if action != verdict.action else None
    if action == A.ALLOW and verdict.action != A.ALLOW:
        # Downgraded all the way: nothing reaches the agent.
        feedback = None
    else:
        feedback = verdict.feedback

    incident = None
    if verdict.incident:
        incident = await _raise_incident(session, execution, task, verdict, action, held_back)

    decision = SupervisorDecision(
        user_id=execution.user_id,
        execution_id=execution.id,
        agent_id=execution.agent_id,
        task_id=task.id if task else None,
        event_type=event_type,
        tool_name=tool_name,
        action=action,
        reason=verdict.reason,
        feedback=feedback,
        rule=verdict.rule,
        mode=mode,
        intended_action=intended,
        held_back_reason=held_back,
        incident_id=incident.id if incident else None,
    )
    session.add(decision)
    await session.flush()

    approval = None
    if action == A.REQUEST_APPROVAL:
        timeout = int(config.get("approval_timeout_seconds", 120))
        approval = ApprovalRequest(
            user_id=execution.user_id,
            execution_id=execution.id,
            agent_id=execution.agent_id,
            task_id=task.id if task else None,
            decision_id=decision.id,
            tool_name=tool_name,
            action_preview=_preview(tool_input),
            reason=verdict.reason,
            expires_at=utcnow() + timedelta(seconds=timeout),
            on_timeout=config.get("approval_timeout_action", "deny"),
        )
        session.add(approval)
        await session.flush()
        decision.approval_id = approval.id
    if action == A.REDIRECT and task is not None:
        # The agent is being sent back to work: the task is not finished.
        task.redirects += 1
        task.status = "running"
        task.completed_at = None
        if execution.status == "idle":
            execution.status = "running"

    decision.latency_ms = round((time.perf_counter() - started) * 1000, 2)
    await _trace(session, execution, decision, payload, approval)
    await _notify(execution, decision, approval)
    return decision, approval


async def _raise_incident(
    session: AsyncSession,
    execution: Execution,
    task: Task | None,
    verdict: Verdict,
    action: str,
    held_back: str | None,
) -> Incident:
    spec = verdict.incident or {}
    query = (
        select(Incident)
        .where(Incident.execution_id == execution.id)
        .where(Incident.category == spec["category"])
        .where(Incident.task_id == (task.id if task else None))
    )
    incident = (await session.execute(query)).scalars().first()
    if incident is None:
        incident = Incident(
            execution_id=execution.id,
            agent_id=execution.agent_id,
            user_id=execution.user_id,
            task_id=task.id if task else None,
            category=spec["category"],
            source="decision",
            status=IncidentStatus.OPEN,
            severity=spec["severity"],
            title=spec["title"],
            description=spec.get("description") or verdict.reason,
            evidence={},
        )
        session.add(incident)
    occurrences = (incident.evidence or {}).get("occurrences", 0) + 1
    incident.evidence = {**spec.get("evidence", {}), "occurrences": occurrences}
    incident.severity = spec["severity"]
    incident.recommended_action = verdict.feedback
    incident.action_taken = f"Decision: {action}" + (f" ({held_back})" if held_back else "")
    incident.updated_at = utcnow()
    await session.flush()
    await publish_incident(execution, incident)
    return incident


async def _trace(
    session: AsyncSession,
    execution: Execution,
    decision: SupervisorDecision,
    payload: dict[str, Any],
    approval: ApprovalRequest | None,
) -> None:
    """Write the decision into the session timeline (non-ALLOW only)."""
    rows: list[CanonicalEvent] = []
    now = utcnow()
    if decision.action != A.ALLOW or decision.intended_action:
        rows.append(
            CanonicalEvent(
                event_id=f"decision-{decision.id}",
                event_type=E.SUPERVISOR_DECISION,
                timestamp=now,
                payload={
                    "decision_id": decision.id,
                    "action": decision.action,
                    "intended_action": decision.intended_action,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "tool_name": decision.tool_name,
                    "held_back": decision.held_back_reason,
                },
            )
        )
    if decision.action in (A.BLOCK, A.STOP) and payload.get("tool_call_id"):
        rows.append(
            CanonicalEvent(
                event_id=f"blocked-{decision.id}",
                event_type=E.TOOL_CALL_BLOCKED,
                timestamp=now,
                payload={
                    "tool_name": decision.tool_name,
                    "tool_call_id": payload.get("tool_call_id"),
                    "reason": decision.reason,
                },
            )
        )
    if approval is not None and decision.event_type != E.APPROVAL_REQUESTED:
        rows.append(
            CanonicalEvent(
                event_id=f"approval-{approval.id}",
                event_type=E.APPROVAL_REQUESTED,
                timestamp=now,
                payload={
                    "approval_id": approval.id,
                    "tool_name": approval.tool_name,
                    "reason": approval.reason,
                    "expires_at": approval.expires_at.isoformat(),
                },
            )
        )
    if rows:
        await store_canonical(session, execution=execution, incoming=rows)


async def _notify(
    execution: Execution, decision: SupervisorDecision, approval: ApprovalRequest | None
) -> None:
    if decision.action == A.ALLOW and approval is None:
        return
    try:
        await get_bus().publish(
            user_channel(execution.user_id),
            {
                "type": "decision.created",
                "execution_id": execution.id,
                "decision": {
                    "id": decision.id,
                    "action": decision.action,
                    "rule": decision.rule,
                    "reason": decision.reason,
                    "tool_name": decision.tool_name,
                },
                "approval_id": approval.id if approval else None,
            },
        )
    except Exception:  # pragma: no cover
        logger.exception("failed to publish decision %s", decision.id)


def new_event_id() -> str:
    return uuid.uuid4().hex
