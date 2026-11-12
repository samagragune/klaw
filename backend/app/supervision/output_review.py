"""Output evaluator: did the work meet the task?

The one failure no deterministic rule can see: a run that is fast, cheap and
error-free but did not do what was asked - or answered from memory after its
lookups failed.

Tiered to keep cost down (``evaluation_strategy`` in the brief):

1. The **fast** model screens every output.
2. Only a confident failing verdict is **confirmed** by the deep model. The
   deep verdict is final. Passing outputs never reach the expensive model.

Rules:

* Runs only when the policy opts in, an operator asks, or completion review
  is enabled - it discloses task content to the configured provider.
* An incident is raised only on a confirmed ``satisfies_objective = false``
  with ``high`` or ``medium`` confidence, labelled ``source="llm"``.
* No output -> no model call at all; the verdict records why.
* Any failure degrades to "not evaluated" and never touches session status.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, Incident, IncidentSeverity, IncidentStatus, Task, utcnow
from app.supervision.evaluator import evaluate_output
from app.supervision.providers import LLMProvider, get_provider

logger = logging.getLogger(__name__)

CATEGORY = "poor_output"
RAISE_ON_CONFIDENCE = {"high", "medium"}

_background: set[asyncio.Task[Any]] = set()


def _fails(result: dict[str, Any]) -> bool:
    return (
        result.get("satisfies_objective") is False
        and str(result.get("confidence", "")).lower() in RAISE_ON_CONFIDENCE
    )


async def review_task_output(
    session: AsyncSession,
    *,
    execution: Execution,
    task: Task | None,
    output: str | None = None,
    provider: LLMProvider | None = None,
    raise_incident: bool = True,
) -> tuple[dict[str, Any], Incident | None]:
    """Evaluate one task's output (or the session's, for single-task SDK runs).

    Caller commits. Returns ``(verdict, incident_or_None)``.
    """
    if task is not None and output and not task.output_summary:
        task.output_summary = output
    has_output = (task.output_summary if task else None) or execution.output_summary
    if not has_output:
        verdict: dict[str, Any] = {
            "satisfies_objective": None,
            "confidence": "none",
            "reasoning": "No output was captured, so there is nothing to evaluate. "
            "Enable output capture in the adapter to use output review.",
            "evaluator": None,
            "skipped": True,
        }
    elif provider is not None:
        verdict = await evaluate_output(session, execution=execution, task=task, provider=provider)
    else:
        fast = get_provider("fast")
        verdict = await evaluate_output(
            session, execution=execution, task=task, provider=fast, purpose="output_screen"
        )
        deep = get_provider("deep")
        if _fails(verdict) and deep.model != fast.model:
            screen = verdict
            verdict = await evaluate_output(
                session, execution=execution, task=task, provider=deep, purpose="output_confirm"
            )
            verdict["screened_by"] = screen.get("evaluator")
            verdict["screen_verdict"] = {
                k: screen.get(k) for k in ("satisfies_objective", "confidence", "gaps")
            }

    record = {"evaluated_at": utcnow().isoformat(), **verdict}
    if task is not None:
        task.evaluation = record
    extra = dict(execution.extra or {})
    extra["output_evaluation"] = record
    execution.extra = extra

    if not raise_incident or not _fails(verdict):
        return verdict, None

    gaps = [str(g) for g in (verdict.get("gaps") or [])][:10]
    reasoning = str(verdict.get("reasoning") or "").strip()
    query = (
        select(Incident)
        .where(Incident.execution_id == execution.id)
        .where(Incident.category == CATEGORY)
    )
    query = (
        query.where(Incident.task_id == task.id)
        if task is not None
        else query.where(Incident.task_id.is_(None))
    )
    incident = (await session.execute(query)).scalars().first()
    if incident is None:
        incident = Incident(
            execution_id=execution.id,
            agent_id=execution.agent_id,
            user_id=execution.user_id,
            task_id=task.id if task else None,
            category=CATEGORY,
            source="llm",
            status=IncidentStatus.OPEN,
        )
        session.add(incident)
    incident.severity = IncidentSeverity.MEDIUM
    incident.title = "Output does not satisfy the task"
    incident.description = (
        "The work completed without errors, but the output evaluator judged that it "
        "does not meet the stated objective." + (f"\n\n{reasoning}" if reasoning else "")
    )
    incident.evidence = {
        "evaluator": verdict.get("evaluator"),
        "screened_by": verdict.get("screened_by"),
        "confidence": verdict.get("confidence"),
        "task": (task.objective if task else None) or execution.task,
        "output_summary": (task.output_summary if task else None) or execution.output_summary,
        "gaps": gaps,
    }
    incident.recommended_action = (
        "Review the output against the task. Gaps identified: " + "; ".join(gaps)
        if gaps
        else "Review the output against the task."
    )
    incident.diagnosis = reasoning or None
    incident.diagnosis_source = verdict.get("evaluator")
    incident.updated_at = utcnow()
    await session.flush()
    return verdict, incident


async def review_output(
    session: AsyncSession, *, execution: Execution, provider: LLMProvider | None = None
) -> tuple[dict[str, Any], Incident | None]:
    """Review the session's most recent task (or the session itself)."""
    task = None
    if execution.current_task_id:
        task = await session.get(Task, execution.current_task_id)
    return await review_task_output(session, execution=execution, task=task, provider=provider)


async def _review_in_background(execution_id: str, task_id: str | None) -> None:
    from app.db import SessionLocal
    from app.supervision.engine import publish_incident

    try:
        async with SessionLocal() as session:
            execution = await session.get(Execution, execution_id)
            if execution is None:
                return
            task = await session.get(Task, task_id) if task_id else None
            if task is None and execution.current_task_id:
                task = await session.get(Task, execution.current_task_id)
            _, incident = await review_task_output(session, execution=execution, task=task)
            await session.commit()
            if incident is not None:
                await publish_incident(execution, incident)
    except Exception:
        logger.exception("output review failed for execution %s", execution_id)
        try:
            from app.db import SessionLocal as _S

            async with _S() as session:
                execution = await session.get(Execution, execution_id)
                if execution is not None:
                    extra = dict(execution.extra or {})
                    extra["output_evaluation"] = {
                        "evaluated_at": utcnow().isoformat(),
                        "satisfies_objective": None,
                        "confidence": "none",
                        "error": "The evaluator call failed; this output was not reviewed.",
                    }
                    execution.extra = extra
                    await session.commit()
        except Exception:  # pragma: no cover
            logger.exception("could not record review failure for %s", execution_id)


def schedule_output_review(execution_id: str, task_id: str | None = None) -> None:
    """Run a review after the current request, without delaying the adapter."""
    task = asyncio.create_task(_review_in_background(execution_id, task_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
