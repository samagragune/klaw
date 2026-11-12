"""Deterministic detectors (loop detector, progress detector, limits).

Pure functions over stored events. No LLM is involved, which is why loops,
runaway budgets and repeated failures are caught with no API key at all.

Scope: limits that describe *one objective* (loops, repeated failures,
tool-call budget, duration, stalls) are evaluated over the current task's
events. Spend limits (tokens, cost) are evaluated over the whole session.
For single-task platforms the two scopes coincide; for Claude Code - where a
session can span hours of unrelated prompts - this is what keeps an hour-old
`git status` from counting toward a loop in the current prompt.

Every detection carries evidence assembled from stored events - ids, sequence
numbers, timestamps - so it can always be traced back to the trace rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import events as E
from app.models import Execution, ExecutionEvent, ExecutionStatus, IncidentSeverity, Task


@dataclass
class Detection:
    category: str
    severity: str
    title: str
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Human-readable remediation. Shown in recommend mode.
    recommended_action: str | None = None
    #: Runtime action control mode may take, if the policy permits it and the
    #: platform supports it.
    suggested_intervention: str | None = None
    #: Text that can be handed to the agent itself as corrective feedback.
    agent_feedback: str | None = None
    task_id: str | None = None


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_ref(event: ExecutionEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "seq": event.seq,
        "event_type": event.event_type,
        "timestamp": _as_aware(event.timestamp).isoformat(),
    }


def _tool_calls(events: list[ExecutionEvent]) -> list[dict[str, Any]]:
    """Pair starts with their outcome via ``tool_call_id``."""
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.payload or {}
        if event.event_type == E.TOOL_CALL_STARTED:
            call = {
                "event": event,
                "tool": str(payload.get("tool_name", "unknown")),
                "args_fp": str(payload.get("args_fingerprint", "")),
                "result_fp": None,
                "outcome": None,
            }
            calls.append(call)
            if payload.get("tool_call_id"):
                by_id[str(payload["tool_call_id"])] = call
        elif event.event_type in (E.TOOL_CALL_COMPLETED, E.TOOL_CALL_FAILED):
            call = by_id.get(str(payload.get("tool_call_id", "")))
            if call is not None:
                call["outcome"] = event.event_type
                call["result_fp"] = payload.get("result_fingerprint") or (
                    f"error:{payload.get('error_type') or payload.get('error')}"
                    if event.event_type == E.TOOL_CALL_FAILED
                    else None
                )
    return calls


def loop_groups(events: list[ExecutionEvent]) -> dict[tuple[str, str, str], list[dict]]:
    """Group tool calls that are indistinguishable: same tool, same arguments
    and - where the platform reports it - the same result.

    Requiring an identical result is what separates a loop from legitimate
    repetition: re-reading a file after editing it, or re-running tests after
    a fix, repeats the call but changes the answer. Calls whose result is not
    known (platform does not fingerprint results, or still running) compare on
    arguments alone.
    """
    calls = _tool_calls(events)
    platform_reports_results = any(c["result_fp"] for c in calls)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for call in calls:
        if platform_reports_results and call["outcome"] is None:
            # Still running; it is judged once its result arrives.
            continue
        key = (call["tool"], call["args_fp"], call["result_fp"] or "*")
        groups.setdefault(key, []).append(call)
    return groups


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_repeated_tool_calls(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    threshold = config.get("repeated_tool_call_threshold")
    if not threshold:
        return None

    groups = loop_groups(events)
    worst = max(
        (g for g in groups.items() if len(g[1]) >= threshold),
        key=lambda kv: len(kv[1]),
        default=None,
    )
    if worst is None:
        return None

    (tool, fingerprint, result_fp), group = worst
    count = len(group)
    severity = IncidentSeverity.CRITICAL if count >= threshold * 2 else IncidentSeverity.HIGH
    first = _as_aware(group[0]["event"].timestamp)
    last = _as_aware(group[-1]["event"].timestamp)
    same_result = result_fp != "*"

    return Detection(
        category="repeated_tool_call",
        severity=severity,
        title=f"'{tool}' called {count} times with identical arguments"
        + (" and identical results" if same_result else ""),
        description=(
            f"The agent invoked '{tool}' {count} times with the same argument "
            f"fingerprint between {first.isoformat()} and {last.isoformat()}"
            + (", receiving the same result every time" if same_result else "")
            + f". The configured threshold is {threshold}. Repeating a call that "
            "returns nothing new is the signature of a non-progressing loop."
        ),
        evidence={
            "tool_name": tool,
            "args_fingerprint": fingerprint,
            "result_fingerprint": result_fp if same_result else None,
            "occurrences": count,
            "threshold": threshold,
            "first_seen": first.isoformat(),
            "last_seen": last.isoformat(),
            "window_seconds": round((last - first).total_seconds(), 3),
            "events": [_event_ref(c["event"]) for c in group[:20]],
        },
        recommended_action=(
            f"Inspect why '{tool}' returns a result the agent does not act on. "
            "Consider a per-tool call budget or a de-duplication guard."
        ),
        suggested_intervention="pause",
        agent_feedback=(
            f"You have called {tool} {count} times with exactly the same input"
            + (" and got the same result each time" if same_result else "")
            + ". Repeating it will not produce new information. Change your "
            "approach or report what is blocking you."
        ),
    )


def detect_repeated_errors(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    threshold = config.get("repeated_error_threshold")
    if not threshold:
        return None

    groups: dict[tuple[str, str], list[ExecutionEvent]] = {}
    for event in events:
        payload = event.payload or {}
        if event.event_type == E.TOOL_CALL_FAILED:
            target = str(payload.get("tool_name") or "unknown")
        elif E.is_model_error(event.event_type, payload):
            target = str(payload.get("model") or "model")
        else:
            continue
        error_type = str(payload.get("error_type") or "error")
        groups.setdefault((target, error_type), []).append(event)

    worst = max(groups.items(), key=lambda kv: len(kv[1]), default=None)
    if worst is None or len(worst[1]) < threshold:
        return None

    (target, error_type), group = worst
    sample = (group[-1].payload or {}).get("error")
    return Detection(
        category="repeated_errors",
        severity=IncidentSeverity.HIGH,
        title=f"'{target}' failed {len(group)} times with {error_type}",
        description=(
            f"{len(group)} failures of '{target}' with error type {error_type} were "
            f"recorded, at or above the configured threshold of {threshold}. "
            "The agent is retrying an operation that is not recovering."
        ),
        evidence={
            "target": target,
            "error_type": error_type,
            "failures": len(group),
            "threshold": threshold,
            "last_error": sample,
            "events": [_event_ref(e) for e in group[:20]],
        },
        recommended_action=(
            f"Fix '{target}', or add a failure budget so the agent stops retrying "
            "a dependency that is down."
        ),
        suggested_intervention="pause",
        agent_feedback=(
            f"{target} has now failed {len(group)} times with the same error "
            f"({error_type}). Retrying is not working; try a different approach "
            "or stop and report the failure."
        ),
    )


def detect_tool_call_limit(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    limit = config.get("max_tool_calls")
    starts = [e for e in events if e.event_type == E.TOOL_CALL_STARTED]
    if not limit or len(starts) <= limit:
        return None
    return Detection(
        category="tool_call_limit",
        severity=IncidentSeverity.HIGH,
        title=f"Task exceeded its {limit} tool-call budget",
        description=f"{len(starts)} tool calls recorded against a configured maximum of {limit}.",
        evidence={
            "tool_calls": len(starts),
            "limit": limit,
            "events": [_event_ref(e) for e in starts[-10:]],
        },
        recommended_action="Review the plan the agent is executing.",
        suggested_intervention="pause",
    )


def detect_execution_timeout(
    execution: Execution,
    events: list[ExecutionEvent],
    config: dict[str, Any],
    task: Task | None = None,
) -> Detection | None:
    limit = config.get("max_duration_seconds")
    if not limit or execution.status not in (ExecutionStatus.RUNNING, ExecutionStatus.PAUSED):
        return None
    if task is not None and task.completed_at is not None:
        return None

    started = _as_aware(task.started_at if task is not None else execution.started_at)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if elapsed <= limit:
        return None

    scope = "task" if task is not None else "execution"
    return Detection(
        category="execution_timeout",
        severity=IncidentSeverity.HIGH,
        title=f"The {scope} exceeded its {limit}s duration limit",
        description=(
            f"The current {scope} has been running for {elapsed:.0f}s, past the "
            f"configured limit of {limit}s."
        ),
        evidence={
            "elapsed_seconds": round(elapsed, 1),
            "limit_seconds": limit,
            "started_at": started.isoformat(),
            "status": execution.status,
            "last_event": _event_ref(events[-1]) if events else None,
        },
        recommended_action="Stop it, or raise the duration limit if long runs are expected.",
        suggested_intervention="cancel",
    )


def detect_stall(
    execution: Execution,
    events: list[ExecutionEvent],
    config: dict[str, Any],
    task: Task | None = None,
) -> Detection | None:
    """Progress detector: a running task that has gone quiet.

    A tool call still in flight is not a stall - a long build or test run is
    legitimate - so only silence with nothing outstanding counts.
    """
    limit = config.get("stall_seconds")
    if not limit or execution.status != ExecutionStatus.RUNNING or not events:
        return None
    if task is not None and task.completed_at is not None:
        return None
    in_flight = {
        (e.payload or {}).get("tool_call_id")
        for e in events
        if e.event_type == E.TOOL_CALL_STARTED
    } - {
        (e.payload or {}).get("tool_call_id")
        for e in events
        if e.event_type in (E.TOOL_CALL_COMPLETED, E.TOOL_CALL_FAILED, E.TOOL_CALL_BLOCKED)
    }
    in_flight.discard(None)
    if in_flight:
        return None
    last = _as_aware(events[-1].timestamp)
    silent = (datetime.now(timezone.utc) - last).total_seconds()
    if silent <= limit:
        return None
    return Detection(
        category="stalled",
        severity=IncidentSeverity.MEDIUM,
        title=f"No progress for {silent / 60:.0f} minutes",
        description=(
            f"The task is marked running, but nothing has happened for {silent:.0f}s "
            f"(threshold {limit}s) and no tool call is outstanding."
        ),
        evidence={
            "silent_seconds": round(silent, 1),
            "threshold_seconds": limit,
            "last_event": _event_ref(events[-1]),
        },
        recommended_action="Check whether the agent is waiting on input or has hung.",
    )


def detect_token_budget(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    limit = config.get("max_tokens")
    if not limit or execution.token_usage <= limit:
        return None
    return Detection(
        category="token_budget",
        severity=IncidentSeverity.HIGH,
        title=f"Session exceeded its {limit:,} token budget",
        description=(
            f"{execution.token_usage:,} tokens consumed "
            f"({execution.prompt_tokens:,} prompt / {execution.completion_tokens:,} completion) "
            f"against a configured limit of {limit:,}."
        ),
        evidence={
            "token_usage": execution.token_usage,
            "prompt_tokens": execution.prompt_tokens,
            "completion_tokens": execution.completion_tokens,
            "limit": limit,
            "model_calls": sum(1 for e in events if e.event_type == E.MODEL_CALL),
        },
        recommended_action="Review prompt size or retry behaviour.",
        suggested_intervention="pause",
    )


def detect_cost_budget(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    limit = config.get("max_cost_usd")
    if not limit or execution.estimated_cost is None or execution.estimated_cost <= limit:
        return None
    unpriced = (execution.extra or {}).get("unpriced_models", [])
    return Detection(
        category="cost_budget",
        severity=IncidentSeverity.HIGH,
        title=f"Estimated cost ${execution.estimated_cost:.4f} exceeded the ${limit} limit",
        description=(
            f"Estimated spend for this session is ${execution.estimated_cost:.4f}, "
            f"above the configured limit of ${limit}."
            + (
                f" {len(unpriced)} model(s) had no known price and are excluded, so "
                "actual spend is higher."
                if unpriced
                else ""
            )
        ),
        evidence={
            "estimated_cost_usd": execution.estimated_cost,
            "limit_usd": limit,
            "token_usage": execution.token_usage,
            "unpriced_models": unpriced,
        },
        recommended_action="Review the model or token budget.",
        suggested_intervention="pause",
    )


TASK_DETECTORS = (detect_repeated_tool_calls, detect_repeated_errors, detect_tool_call_limit)
TIMED_DETECTORS = (detect_execution_timeout, detect_stall)
SESSION_DETECTORS = (detect_token_budget, detect_cost_budget)


def run_detectors(
    execution: Execution,
    events: list[ExecutionEvent],
    config: dict[str, Any],
    task: Task | None = None,
) -> list[Detection]:
    """Run every detector. ``events`` is the whole session, in order."""
    task_events = [e for e in events if e.task_id == task.id] if task is not None else events
    findings: list[Detection] = []
    for detector in TASK_DETECTORS:
        result = detector(execution, task_events, config)
        if result is not None:
            result.task_id = task.id if task else None
            findings.append(result)
    for detector in TIMED_DETECTORS:
        result = detector(execution, task_events, config, task)
        if result is not None:
            result.task_id = task.id if task else None
            findings.append(result)
    for detector in SESSION_DETECTORS:
        result = detector(execution, events, config)
        if result is not None:
            findings.append(result)
    return findings
