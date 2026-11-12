"""LLM evaluation grounded in stored evidence.

Rules this module keeps:

1. The model only sees a digest built from rows actually in the database.
2. The system prompt forbids asserting anything the digest does not contain
   and requires an explicit "insufficient evidence" answer instead.
3. A failure here degrades to "not evaluated". It never blocks or alters
   deterministic detection.

Digests are task-scoped when a task is given: a Claude Code session can run
for hours across unrelated prompts, and judging one answer against all of
that would be both wrong and expensive.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events as E
from app.models import Execution, ExecutionEvent, Incident, Task, utcnow
from app.supervision.llm import ask, parse_json
from app.supervision.providers import EvaluatorUnavailable, LLMProvider, get_provider

logger = logging.getLogger(__name__)

MAX_DIGEST_EVENTS = 120
MAX_PAYLOAD_CHARS = 1500

SYSTEM_PROMPT = """You are the supervisory evaluator inside an observability \
and control plane for autonomous AI agents.

You receive a factual digest of agent activity, assembled from stored \
telemetry. Rules you must follow without exception:

- Use ONLY the facts in the digest. Never invent tool calls, errors, token \
counts, timings or outcomes.
- If the digest does not contain enough information to answer, say so \
explicitly and set confidence to "none".
- Quote concrete values from the digest (tool names, counts, sequence numbers) \
when you justify a conclusion.
- Be concise and technical. No preamble, no reassurance.

Respond with a single JSON object and nothing else."""


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, default=str)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return payload
    return {"truncated": text[:MAX_PAYLOAD_CHARS] + f"... [{len(text)} chars]"}


def build_digest(
    execution: Execution, events: list[ExecutionEvent], task: Task | None = None
) -> dict[str, Any]:
    """Compact, strictly factual representation of a session or one task."""
    if task is not None:
        events = [e for e in events if e.task_id == task.id]
    started = _as_aware(task.started_at if task else execution.started_at)
    completed = _as_aware(task.completed_at if task else execution.completed_at)
    duration = (
        ((completed or datetime.now(timezone.utc)) - started).total_seconds() if started else None
    )

    tool_counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload or {}
        if event.event_type == E.TOOL_CALL_STARTED:
            name = str(payload.get("tool_name", "unknown"))
            tool_counts[name] = tool_counts.get(name, 0) + 1
        elif event.event_type == E.TOOL_CALL_FAILED or E.is_model_error(event.event_type, payload):
            errors.append(
                {
                    "seq": event.seq,
                    "target": payload.get("tool_name") or payload.get("model"),
                    "error_type": payload.get("error_type"),
                    "error": payload.get("error"),
                }
            )

    if len(events) > MAX_DIGEST_EVENTS:
        half = MAX_DIGEST_EVENTS // 2
        sampled = events[:half] + events[-half:]
        omitted = len(events) - len(sampled)
    else:
        sampled, omitted = events, 0

    timeline = [
        {
            "seq": e.seq,
            "type": e.event_type,
            "at": _as_aware(e.timestamp).isoformat(),  # type: ignore[union-attr]
            **({"actor": e.actor} if e.actor else {}),
            "payload": _compact(e.payload or {}),
        }
        for e in sampled
    ]

    subject: dict[str, Any] = {
        "platform": execution.platform,
        "status": task.status if task else execution.status,
        "task": (task.objective if task else None) or execution.task,
        "started_at": started.isoformat() if started else None,
        "completed_at": completed.isoformat() if completed else None,
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "tool_call_count": task.tool_call_count if task else execution.tool_call_count,
        "error_count": task.error_count if task else execution.error_count,
        "output_summary": (task.output_summary if task else None) or execution.output_summary,
    }
    if task is None:
        subject.update(
            {
                "token_usage": execution.token_usage,
                "estimated_cost_usd": execution.estimated_cost,
                "error": execution.error,
            }
        )
    return {
        "subject": "task" if task else "session",
        "execution": subject,
        "tool_call_counts": tool_counts,
        "errors": errors[:25],
        "timeline": timeline,
        "timeline_events_omitted": omitted,
    }


async def load_events(session: AsyncSession, execution_id: str) -> list[ExecutionEvent]:
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id)
        .order_by(ExecutionEvent.seq)
    )
    return list(result.scalars())


async def diagnose_incident(
    session: AsyncSession,
    *,
    incident: Incident,
    execution: Execution,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Attach a model-written diagnosis to an incident (deep tier)."""
    provider = provider or get_provider("deep")
    events = await load_events(session, execution.id)
    task = await session.get(Task, incident.task_id) if incident.task_id else None
    digest = build_digest(execution, events, task)

    prompt = (
        "A supervisor rule raised this incident:\n"
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
        + "\n\nDigest:\n"
        + json.dumps(digest, indent=2, default=str)
        + "\n\nReturn JSON with keys: summary (string), likely_cause (string), "
        "supporting_evidence (array of strings quoting digest values), "
        "recommended_action (string), confidence (one of high|medium|low|none)."
    )
    result = await ask(
        session,
        user_id=execution.user_id,
        purpose="diagnosis",
        provider=provider,
        system=SYSTEM_PROMPT,
        prompt=prompt,
    )
    incident.diagnosis = result.get("summary") or result.get("likely_cause")
    if result.get("likely_cause") and result.get("summary"):
        incident.diagnosis = f"{result['summary']}\n\nLikely cause: {result['likely_cause']}"
    if result.get("recommended_action"):
        incident.recommended_action = result["recommended_action"]
    incident.diagnosis_source = f"{provider.name}:{provider.model}"
    incident.updated_at = utcnow()
    return result


async def evaluate_progress(
    session: AsyncSession,
    *,
    execution: Execution,
    task: Task | None = None,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Is a running task advancing toward its objective? (fast tier)"""
    provider = provider or get_provider("fast")
    digest = build_digest(execution, await load_events(session, execution.id), task)
    prompt = (
        "Assess whether this activity is making meaningful progress toward its "
        "stated task.\n\n"
        + json.dumps(digest, indent=2, default=str)
        + '\n\nReturn JSON with keys: making_progress (true|false|null), reasoning '
        "(string), stalled_since_seq (integer or null), supporting_evidence (array "
        "of strings), confidence (high|medium|low|none)."
    )
    return await ask(
        session,
        user_id=execution.user_id,
        purpose="progress",
        provider=provider,
        system=SYSTEM_PROMPT,
        prompt=prompt,
    )


OUTPUT_INSTRUCTIONS = (
    "Assess whether the output satisfies its stated task. If no output was "
    "captured, say so rather than guessing. A value ending in '... [N chars]' "
    "was truncated by telemetry capture, not by the agent: do not count content "
    "beyond the cut as missing.\n\n"
    "Also check grounding. Where the timeline shows tool calls, the output's "
    "factual claims should be supported by successful tool results in it. A "
    "claim with no supporting result - above all when the tool calls that should "
    "have produced it failed - is unverified: list it as a gap, and if the core "
    "answer rests on unverified claims the objective is not satisfied, however "
    "plausible the claims look. Do not judge whether the claims are true from "
    "your own knowledge. If tool results were not captured (no previews in the "
    "timeline), do not treat their absence as a grounding failure.\n\n"
)


async def evaluate_output(
    session: AsyncSession,
    *,
    execution: Execution,
    task: Task | None = None,
    provider: LLMProvider | None = None,
    purpose: str = "output_review",
) -> dict[str, Any]:
    provider = provider or get_provider("deep")
    digest = build_digest(execution, await load_events(session, execution.id), task)
    prompt = (
        OUTPUT_INSTRUCTIONS
        + json.dumps(digest, indent=2, default=str)
        + "\n\nReturn JSON with keys: satisfies_objective (true|false|null), "
        "grounded (true|false|null - whether the core claims are supported by "
        "successful tool results), gaps (array of strings), reasoning (string), "
        "confidence (high|medium|low|none)."
    )
    result = await ask(
        session,
        user_id=execution.user_id,
        purpose=purpose,
        provider=provider,
        system=SYSTEM_PROMPT,
        prompt=prompt,
    )
    result["evaluator"] = f"{provider.name}:{provider.model}"
    return result


#: Public alias - the supervisor chat endpoint reuses the same lenient parser.
parse_json_response = parse_json

__all__ = [
    "EvaluatorUnavailable",
    "SYSTEM_PROMPT",
    "parse_json_response",
    "build_digest",
    "diagnose_incident",
    "evaluate_output",
    "evaluate_progress",
]
