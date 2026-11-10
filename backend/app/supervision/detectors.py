"""Deterministic detectors.

These are pure functions over stored execution state. They carry no LLM
dependency, which is why the product can detect loops, runaway budgets and
repeated failures with no API key configured at all.

Every detection carries ``evidence`` assembled from real stored events -
event ids, sequence numbers and timestamps - so an incident can always be
traced back to the trace rows that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import events as E
from app.models import Execution, ExecutionEvent, ExecutionStatus, IncidentSeverity


@dataclass
class Detection:
    category: str
    severity: str
    title: str
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Human-readable remediation. Shown in assist mode, executed in autopilot.
    recommended_action: str | None = None
    #: Control action autopilot would take, if the policy permits it.
    suggested_intervention: str | None = None


def _as_aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; normalise before arithmetic."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_ref(event: ExecutionEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "seq": event.seq,
        "event_type": event.event_type,
        "timestamp": _as_aware(event.timestamp).isoformat(),
    }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_repeated_tool_calls(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    """Identical tool invocations repeated past the configured threshold.

    The SDK sends ``args_fingerprint`` (a hash of canonicalised arguments) with
    every ``tool.started`` event, so this works even in metadata-only mode
    where raw arguments are never transmitted.
    """
    threshold = config.get("repeated_tool_call_threshold")
    if not threshold:
        return None

    groups: dict[tuple[str, str], list[ExecutionEvent]] = {}
    for event in events:
        if event.event_type != E.TOOL_STARTED:
            continue
        payload = event.payload or {}
        tool = str(payload.get("tool_name", "unknown"))
        fingerprint = str(payload.get("args_fingerprint", ""))
        groups.setdefault((tool, fingerprint), []).append(event)

    worst: tuple[tuple[str, str], list[ExecutionEvent]] | None = None
    for key, group in groups.items():
        if len(group) >= threshold and (worst is None or len(group) > len(worst[1])):
            worst = (key, group)
    if worst is None:
        return None

    (tool, fingerprint), group = worst
    count = len(group)
    severity = (
        IncidentSeverity.CRITICAL if count >= threshold * 2 else IncidentSeverity.HIGH
    )
    first = _as_aware(group[0].timestamp)
    last = _as_aware(group[-1].timestamp)

    return Detection(
        category="repeated_tool_call",
        severity=severity,
        title=f"Tool '{tool}' called {count} times with identical arguments",
        description=(
            f"The agent invoked '{tool}' {count} times with the same argument "
            f"fingerprint between {first.isoformat()} and {last.isoformat()}. "
            f"The configured threshold is {threshold}. Repeating an identical "
            "call without a changing result is the signature of a non-progressing loop."
        ),
        evidence={
            "tool_name": tool,
            "args_fingerprint": fingerprint,
            "occurrences": count,
            "threshold": threshold,
            "first_seen": first.isoformat(),
            "last_seen": last.isoformat(),
            "window_seconds": round((last - first).total_seconds(), 3),
            "events": [_event_ref(e) for e in group[:20]],
        },
        recommended_action=(
            f"Pause the execution and inspect why '{tool}' returns a result the agent "
            "does not act on. Consider a per-tool call budget or a de-duplication guard."
        ),
        suggested_intervention="pause",
    )


def detect_execution_timeout(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    limit = config.get("max_duration_seconds")
    if not limit or execution.status in ExecutionStatus.TERMINAL:
        return None

    started = _as_aware(execution.started_at)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if elapsed <= limit:
        return None

    return Detection(
        category="execution_timeout",
        severity=IncidentSeverity.HIGH,
        title=f"Execution exceeded its {limit}s duration limit",
        description=(
            f"The execution has been {execution.status} for {elapsed:.0f}s, past the "
            f"configured limit of {limit}s, with {execution.tool_call_count} tool calls "
            f"and {execution.event_count} events recorded."
        ),
        evidence={
            "elapsed_seconds": round(elapsed, 1),
            "limit_seconds": limit,
            "started_at": started.isoformat(),
            "status": execution.status,
            "tool_calls": execution.tool_call_count,
            "last_event": _event_ref(events[-1]) if events else None,
        },
        recommended_action="Cancel the execution, or raise the duration limit if long runs are expected.",
        suggested_intervention="cancel",
    )


def detect_tool_call_limit(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    limit = config.get("max_tool_calls")
    if not limit or execution.tool_call_count <= limit:
        return None

    return Detection(
        category="tool_call_limit",
        severity=IncidentSeverity.HIGH,
        title=f"Execution exceeded its {limit} tool-call budget",
        description=(
            f"{execution.tool_call_count} tool calls have been recorded against a "
            f"configured maximum of {limit}."
        ),
        evidence={
            "tool_calls": execution.tool_call_count,
            "limit": limit,
            "events": [_event_ref(e) for e in events if e.event_type == E.TOOL_STARTED][-10:],
        },
        recommended_action="Pause and review the plan the agent is executing.",
        suggested_intervention="pause",
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
        title=f"Execution exceeded its {limit:,} token budget",
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
            "llm_calls": sum(1 for e in events if e.event_type == E.LLM_COMPLETED),
        },
        recommended_action="Pause the execution and review prompt size or retry behaviour.",
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
            f"Estimated spend for this execution is ${execution.estimated_cost:.4f}, "
            f"above the configured limit of ${limit}."
            + (
                f" Note: {len(unpriced)} model(s) had no known price and are excluded "
                "from this figure, so actual spend is higher."
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
        recommended_action="Pause the execution and review the model or token budget.",
        suggested_intervention="pause",
    )


def detect_repeated_errors(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> Detection | None:
    threshold = config.get("repeated_error_threshold")
    if not threshold:
        return None

    groups: dict[tuple[str, str], list[ExecutionEvent]] = {}
    for event in events:
        if event.event_type not in (E.TOOL_FAILED, E.LLM_FAILED):
            continue
        payload = event.payload or {}
        target = str(payload.get("tool_name") or payload.get("model") or "unknown")
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
            f"Pause and fix '{target}', or add a failure budget so the agent stops "
            "retrying a dependency that is down."
        ),
        suggested_intervention="pause",
    )


ALL_DETECTORS = (
    detect_repeated_tool_calls,
    detect_execution_timeout,
    detect_tool_call_limit,
    detect_token_budget,
    detect_cost_budget,
    detect_repeated_errors,
)


def run_detectors(
    execution: Execution, events: list[ExecutionEvent], config: dict[str, Any]
) -> list[Detection]:
    findings: list[Detection] = []
    for detector in ALL_DETECTORS:
        result = detector(execution, events, config)
        if result is not None:
            findings.append(result)
    return findings
