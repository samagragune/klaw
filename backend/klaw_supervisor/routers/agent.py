"""What the Klaw agent is doing: its status, its reasoning feed, its verdicts."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from klaw_supervisor import events as E
from klaw_supervisor.models import (
    DecisionAction as A,
    EvaluatorCall,
    Execution,
    ExecutionStatus,
    SupervisorDecision,
    Task,
    utcnow,
)
from klaw_supervisor.security import CurrentUser, DbSession
from klaw_supervisor.supervision import agent as klaw_agent
from klaw_supervisor.supervision.engine import effective_policy, load_events, policy_for_execution

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agent", tags=["agent"])

_background: set[asyncio.Task] = set()


@router.get("")
async def agent_status(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Is the agent thinking, with which model, and what has it done today."""
    since = utcnow() - timedelta(hours=24)
    mode, config, _ = await effective_policy(session, user_id=user.id, agent_id="", platform=None)
    rows = (
        await session.execute(
            select(SupervisorDecision.rule, SupervisorDecision.action, func.count())
            .where(SupervisorDecision.user_id == user.id)
            .where(SupervisorDecision.created_at >= since)
            .where(SupervisorDecision.rule.like("agent.%"))
            .group_by(SupervisorDecision.rule, SupervisorDecision.action)
        )
    ).all()
    by_rule: dict[str, int] = {}
    for rule, _action, count in rows:
        by_rule[rule] = by_rule.get(rule, 0) + count
    calls = (
        await session.execute(
            select(
                EvaluatorCall.purpose,
                func.count(),
                func.avg(EvaluatorCall.latency_ms),
                func.coalesce(func.sum(EvaluatorCall.estimated_cost), 0.0),
                func.sum(case((EvaluatorCall.ok.is_(True), 1), else_=0)),
            )
            .where(EvaluatorCall.user_id == user.id)
            .where(EvaluatorCall.created_at >= since)
            .where(EvaluatorCall.purpose.like("agent.%"))
            .group_by(EvaluatorCall.purpose)
        )
    ).all()
    live = (
        await session.execute(
            select(func.count())
            .select_from(Execution)
            .where(Execution.user_id == user.id)
            .where(Execution.status.in_(tuple(ExecutionStatus.LIVE)))
        )
    ).scalar_one()
    return {
        **klaw_agent.status(),
        "enabled": bool(config.get("agent_enabled", True)),
        "mode": mode,
        "settings": {
            key: config.get(key)
            for key in (
                "agent_enabled",
                "agent_review_completion",
                "agent_max_attempts",
                "agent_monitor",
                "agent_monitor_interval_seconds",
                "agent_action_timeout_seconds",
            )
        },
        "live_sessions": live,
        "last_24h": {
            "actions_reviewed": sum(v for k, v in by_rule.items() if not k.startswith("agent.completion")),
            "allowed": by_rule.get("agent.allow", 0),
            "guided": by_rule.get("agent.guide", 0) + by_rule.get("agent.guidance", 0),
            "blocked": by_rule.get("agent.block", 0),
            "held_for_human": by_rule.get("agent.ask_human", 0),
            "stopped": by_rule.get("agent.stop", 0),
            "completions_passed": by_rule.get("agent.completion.pass", 0),
            "completions_incomplete": by_rule.get("agent.completion.incomplete", 0),
            "completions_retry": by_rule.get("agent.completion.retry", 0),
            "completions_escalated": by_rule.get("agent.completion.escalated", 0),
            "model_calls": [
                {
                    "purpose": purpose,
                    "calls": count,
                    "avg_latency_ms": round(float(latency or 0), 1),
                    "cost_usd": round(float(cost or 0), 6),
                    "ok": int(ok or 0),
                }
                for purpose, count, latency, cost, ok in calls
            ],
        },
    }


@router.get("/feed")
async def agent_feed(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[dict[str, Any]]:
    """The agent's recent decisions and its own assessments, newest first."""
    decisions = (
        await session.execute(
            select(SupervisorDecision, Execution.task, Execution.platform)
            .join(Execution, Execution.id == SupervisorDecision.execution_id)
            .where(SupervisorDecision.user_id == user.id)
            .where(SupervisorDecision.rule.like("agent.%"))
            .order_by(SupervisorDecision.created_at.desc())
            .limit(limit)
        )
    ).all()
    items: list[dict[str, Any]] = []
    for decision, task, platform in decisions:
        rule = decision.rule
        kind = "completion" if rule.startswith("agent.completion") else (
            "guidance" if rule == "agent.guidance" else "action"
        )
        items.append(
            {
                "kind": kind,
                "at": decision.created_at.isoformat(),
                "execution_id": decision.execution_id,
                "session_task": task,
                "platform": platform,
                "tool_name": decision.tool_name,
                "action": decision.action,
                "intended_action": decision.intended_action,
                "rule": rule,
                "verdict": rule.split(".")[-1] if kind == "completion" else None,
                "reasoning": decision.reason,
                "guidance": decision.feedback,
                "held_back": decision.held_back_reason,
                "latency_ms": decision.latency_ms,
            }
        )
    sessions = (
        await session.execute(
            select(Execution)
            .where(Execution.user_id == user.id)
            .order_by(Execution.last_event_at.desc())
            .limit(40)
        )
    ).scalars()
    for execution in sessions:
        state = (execution.context or {}).get("klaw_agent") or {}
        for record in state.get("assessments") or []:
            if record.get("action") in (None, "none") and record.get("assessment") == "on_track":
                continue
            items.append(
                {
                    "kind": "assessment",
                    "at": record.get("at"),
                    "execution_id": execution.id,
                    "session_task": execution.task,
                    "platform": execution.platform,
                    "action": (record.get("action") or "none").upper(),
                    "verdict": record.get("assessment"),
                    "reasoning": record.get("reasoning"),
                    "guidance": record.get("guidance"),
                    "applied": record.get("applied"),
                }
            )
    items.sort(key=lambda item: item.get("at") or "", reverse=True)
    return items[:limit]


@router.get("/sessions/{execution_id}")
async def agent_session_view(execution_id: str, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """The agent's memory, latest assessment and queued guidance for one session."""
    execution = await session.get(Execution, execution_id)
    if execution is None or execution.user_id != user.id:
        return {"memory": [], "assessments": [], "inbox": []}
    state = (execution.context or {}).get("klaw_agent") or {}
    return {
        "memory": state.get("memory") or [],
        "assessments": state.get("assessments") or [],
        "last_assessment": state.get("last_assessment"),
        "inbox": state.get("inbox") or [],
        "plans": state.get("plans") or {},
        "investigations": state.get("investigations") or [],
    }


# ---------------------------------------------------------------------------
# Completion review for event-only platforms (no decision point to redirect)
# ---------------------------------------------------------------------------

async def _review_in_background(execution_id: str, task_id: str) -> None:
    from klaw_supervisor.db import SessionLocal

    try:
        async with SessionLocal() as session:
            execution = await session.get(Execution, execution_id)
            task = await session.get(Task, task_id)
            if execution is None or task is None or (task.evaluation or {}).get("by") == "klaw-agent":
                return
            mode, config, _ = await policy_for_execution(session, execution)
            events = [e for e in await load_events(session, execution.id) if e.task_id == task.id]
            completed = next(
                (e for e in reversed(events) if e.event_type == E.TASK_COMPLETED), None
            )
            output = (completed.payload or {}).get("output") if completed else None
            result = await klaw_agent.review_completion(
                session, execution=execution, task=task, events=events,
                output=output or task.output_summary, mode=mode, config=config,
            )
            if result is None:
                return
            task.evaluation = {
                "verdict": result.verdict,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "missing": result.missing,
                "feedback": result.feedback,
                "attempt": task.redirects + 1,
                "by": "klaw-agent",
                "at": utcnow().isoformat(),
            }
            session.add(
                SupervisorDecision(
                    user_id=execution.user_id,
                    execution_id=execution.id,
                    agent_id=execution.agent_id,
                    task_id=task.id,
                    event_type=E.TASK_COMPLETED,
                    action=A.ALLOW if result.verdict == "pass" else A.ESCALATE_TO_HUMAN,
                    reason=result.reasoning,
                    feedback=result.feedback,
                    rule=f"agent.completion.{result.verdict}",
                    mode=mode,
                    held_back_reason=None if result.verdict == "pass" else (
                        "This platform reported completion as an event, so the agent "
                        "could not be sent back; recorded for a human."
                    ),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("agent completion review failed for %s", execution_id)


def schedule_completion_review(execution_id: str, task_id: str) -> None:
    task = asyncio.create_task(_review_in_background(execution_id, task_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


# ---------------------------------------------------------------------------
# Planning when a task starts (background: never delays the supervised agent)
# ---------------------------------------------------------------------------

async def _plan_in_background(execution_id: str, task_id: str) -> None:
    from klaw_supervisor.db import SessionLocal

    try:
        async with SessionLocal() as session:
            execution = await session.get(Execution, execution_id)
            task = await session.get(Task, task_id)
            if execution is None or task is None:
                return
            mode, config, _ = await policy_for_execution(session, execution)
            before = klaw_agent.snapshot(execution)
            await klaw_agent.plan_task(session, execution, task, mode, config)
            await klaw_agent.merge_after(session, execution, before)
            await session.commit()
    except Exception:
        logger.exception("agent planning failed for %s", execution_id)


def schedule_plan(execution_id: str, task_id: str) -> None:
    task = asyncio.create_task(_plan_in_background(execution_id, task_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


# ---------------------------------------------------------------------------
# Talking to the agent
# ---------------------------------------------------------------------------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    execution_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


@router.post("/chat")
async def agent_chat(body: ChatIn, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Ask the Klaw agent something, or tell it to do something, about a session.

    Without ``execution_id`` the most recently active session is used.
    """
    from klaw_supervisor.supervision.engine import execution_capabilities

    query = select(Execution).where(Execution.user_id == user.id)
    if body.execution_id:
        query = query.where(Execution.id == body.execution_id)
    else:
        query = query.order_by(Execution.last_event_at.desc())
    execution = (await session.execute(query.limit(1))).scalars().first()
    if execution is None:
        return {"answer": "There is no session to talk about yet.", "steps": [], "effects": []}
    task = await session.get(Task, execution.current_task_id) if execution.current_task_id else None
    mode, config, _ = await policy_for_execution(session, execution)
    capabilities = await execution_capabilities(session, execution)
    before = klaw_agent.snapshot(execution)
    result = await klaw_agent.converse(
        session, execution, task, body.message, mode, config, capabilities, body.history
    )
    await klaw_agent.merge_after(session, execution, before)
    await session.commit()
    return {**result, "execution_id": execution.id}
