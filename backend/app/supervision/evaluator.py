"""LLM evaluation grounded in stored execution evidence.

Three rules shape this module:

1. The model only ever sees a digest built from rows actually in the database.
2. The system prompt forbids asserting anything the digest does not contain,
   and requires an explicit "insufficient evidence" answer instead.
3. A failure here degrades to no diagnosis. It never blocks or alters
   deterministic detection, which is what the incident itself rests on.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.models import Execution, ExecutionEvent, Incident, utcnow
from app.supervision.providers import EvaluatorUnavailable, LLMProvider, get_provider

logger = logging.getLogger(__name__)

MAX_DIGEST_EVENTS = 120

SYSTEM_PROMPT = """You are the supervisory evaluator inside Reliability, an observability \
and control plane for autonomous AI agents.

You receive a factual digest of ONE agent execution, assembled from stored \
telemetry. Rules you must follow without exception:

- Use ONLY the facts in the digest. Never invent tool calls, errors, token \
counts, timings or outcomes.
- If the digest does not contain enough information to answer, say so \
explicitly and set confidence to "none".
- Quote concrete values from the digest (tool names, counts, timestamps) when \
you justify a conclusion.
- Be concise and technical. No preamble, no reassurance.

Respond with a single JSON object and nothing else."""


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_digest(execution: Execution, events: list[ExecutionEvent]) -> dict[str, Any]:
    """Compact, strictly factual representation of an execution."""
    started = _as_aware(execution.started_at)
    completed = _as_aware(execution.completed_at)
    duration = (
        ((completed or datetime.now(timezone.utc)) - started).total_seconds()
        if started
        else None
    )

    tool_counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload or {}
        if event.event_type == E.TOOL_STARTED:
            name = str(payload.get("tool_name", "unknown"))
            tool_counts[name] = tool_counts.get(name, 0) + 1
        elif event.event_type in (E.TOOL_FAILED, E.LLM_FAILED):
            errors.append(
                {
                    "seq": event.seq,
                    "target": payload.get("tool_name") or payload.get("model"),
                    "error_type": payload.get("error_type"),
                    "error": payload.get("error"),
                }
            )

    # Keep the head and tail of the trace: the opening moves and what it was
    # doing when things went wrong are the parts that carry signal.
    if len(events) > MAX_DIGEST_EVENTS:
        half = MAX_DIGEST_EVENTS // 2
        sampled = events[:half] + events[-half:]
        omitted = len(events) - len(sampled)
    else:
        sampled = events
        omitted = 0

    timeline = [
        {
            "seq": e.seq,
            "type": e.event_type,
            "at": _as_aware(e.timestamp).isoformat(),  # type: ignore[union-attr]
            "payload": e.payload,
        }
        for e in sampled
    ]

    return {
        "execution": {
            "id": execution.id,
            "status": execution.status,
            "task": execution.task,
            "started_at": started.isoformat() if started else None,
            "completed_at": completed.isoformat() if completed else None,
            "duration_seconds": round(duration, 2) if duration is not None else None,
            "event_count": execution.event_count,
            "tool_call_count": execution.tool_call_count,
            "error_count": execution.error_count,
            "token_usage": execution.token_usage,
            "estimated_cost_usd": execution.estimated_cost,
            "error": execution.error,
            "output_summary": execution.output_summary,
        },
        "tool_call_counts": tool_counts,
        "errors": errors[:25],
        "timeline": timeline,
        "timeline_events_omitted": omitted,
    }


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a model response, tolerating fenced code blocks."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        value = json.loads(cleaned.strip())
    except json.JSONDecodeError:
        return {"summary": cleaned.strip()[:2000], "confidence": "unparsed"}
    return value if isinstance(value, dict) else {"summary": str(value)}


async def _load_events(session: AsyncSession, execution_id: str) -> list[ExecutionEvent]:
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id)
        .order_by(ExecutionEvent.seq)
    )
    return list(result.scalars())


async def _ask(provider: LLMProvider, prompt: str) -> dict[str, Any]:
    raw = await provider.complete(SYSTEM_PROMPT, prompt)
    return _parse_json(raw)


async def diagnose_incident(
    session: AsyncSession,
    *,
    incident: Incident,
    execution: Execution,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Attach a model-written diagnosis to a deterministically raised incident."""
    provider = provider or get_provider()
    events = await _load_events(session, execution.id)
    digest = build_digest(execution, events)

    prompt = (
        "A deterministic detector raised this incident:\n"
        + json.dumps(
            {
                "category": incident.category,
                "severity": incident.severity,
                "title": incident.title,
                "description": incident.description,
                "evidence": incident.evidence,
            },
            indent=2,
            default=str,
        )
        + "\n\nExecution digest:\n"
        + json.dumps(digest, indent=2, default=str)
        + "\n\nReturn JSON with keys: summary (string), likely_cause (string), "
        "supporting_evidence (array of strings quoting digest values), "
        "recommended_action (string), confidence (one of high|medium|low|none)."
    )

    result = await _ask(provider, prompt)
    incident.diagnosis = result.get("summary") or result.get("likely_cause")
    if result.get("likely_cause") and result.get("summary"):
        incident.diagnosis = f"{result['summary']}\n\nLikely cause: {result['likely_cause']}"
    if result.get("recommended_action"):
        incident.recommended_action = result["recommended_action"]
    incident.diagnosis_source = f"{provider.name}:{provider.model}"
    incident.updated_at = utcnow()
    return result


async def evaluate_progress(
    session: AsyncSession, *, execution: Execution, provider: LLMProvider | None = None
) -> dict[str, Any]:
    """Judge whether a running execution is advancing toward its objective."""
    provider = provider or get_provider()
    events = await _load_events(session, execution.id)
    digest = build_digest(execution, events)
    prompt = (
        "Assess whether this execution is making meaningful progress toward its "
        "stated task.\n\n"
        + json.dumps(digest, indent=2, default=str)
        + '\n\nReturn JSON with keys: making_progress (true|false|null), reasoning '
        "(string), stalled_since_seq (integer or null), supporting_evidence (array "
        "of strings), confidence (high|medium|low|none)."
    )
    return await _ask(provider, prompt)


async def evaluate_output(
    session: AsyncSession, *, execution: Execution, provider: LLMProvider | None = None
) -> dict[str, Any]:
    """Judge whether the produced output satisfies the requested objective."""
    provider = provider or get_provider()
    events = await _load_events(session, execution.id)
    digest = build_digest(execution, events)
    prompt = (
        "Assess whether the execution's output satisfies its stated task. If no "
        "output was captured, say so rather than guessing.\n\n"
        + json.dumps(digest, indent=2, default=str)
        + "\n\nReturn JSON with keys: satisfies_objective (true|false|null), "
        "gaps (array of strings), reasoning (string), confidence "
        "(high|medium|low|none)."
    )
    return await _ask(provider, prompt)


#: Public alias - the supervisor chat endpoint reuses the same lenient parser.
parse_json_response = _parse_json

__all__ = [
    "EvaluatorUnavailable",
    "SYSTEM_PROMPT",
    "parse_json_response",
    "build_digest",
    "diagnose_incident",
    "evaluate_output",
    "evaluate_progress",
]
