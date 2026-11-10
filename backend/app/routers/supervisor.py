"""Conversational supervisor.

The question is answered against a digest built from the asking user's own
rows. If no evaluator is configured the endpoint says so plainly instead of
producing a plausible-sounding answer from nothing - the workspace snapshot
is still returned, so the dashboard can show the facts either way.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import Agent, Execution, Incident, IncidentStatus, Intervention
from app.security import CurrentUser, DbSession
from app.supervision.evaluator import SYSTEM_PROMPT, parse_json_response
from app.supervision.providers import (
    EvaluatorUnavailable,
    get_provider,
    provider_configured,
)

router = APIRouter(prefix="/v1/supervisor", tags=["supervisor"])

LOOKBACK_DAYS = 7
MAX_ROWS = 40


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    agent_id: str | None = None
    execution_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    grounded_in: dict[str, Any]
    evaluator: str | None
    #: Present when the question could not be answered by a model.
    unavailable_reason: str | None = None


async def build_workspace_digest(
    session, user_id: str, *, agent_id: str | None = None, execution_id: str | None = None
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    agents = list(
        (await session.execute(select(Agent).where(Agent.user_id == user_id))).scalars()
    )

    exec_query = (
        select(Execution)
        .where(Execution.user_id == user_id)
        .where(Execution.started_at >= since)
        .order_by(Execution.started_at.desc())
        .limit(MAX_ROWS)
    )
    if agent_id:
        exec_query = exec_query.where(Execution.agent_id == agent_id)
    if execution_id:
        exec_query = exec_query.where(Execution.id == execution_id)
    executions = list((await session.execute(exec_query)).scalars())

    incident_query = (
        select(Incident)
        .where(Incident.user_id == user_id)
        .order_by(Incident.created_at.desc())
        .limit(MAX_ROWS)
    )
    if agent_id:
        incident_query = incident_query.where(Incident.agent_id == agent_id)
    if execution_id:
        incident_query = incident_query.where(Incident.execution_id == execution_id)
    incidents = list((await session.execute(incident_query)).scalars())

    interventions = list(
        (
            await session.execute(
                select(Intervention)
                .where(Intervention.user_id == user_id)
                .order_by(Intervention.created_at.desc())
                .limit(MAX_ROWS)
            )
        ).scalars()
    )

    names = {a.id: a.name for a in agents}
    return {
        "window_days": LOOKBACK_DAYS,
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "framework": a.framework,
                "status": a.status,
            }
            for a in agents
        ],
        "executions": [
            {
                "id": e.id,
                "agent": names.get(e.agent_id, e.agent_id),
                "status": e.status,
                "task": e.task,
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "tool_calls": e.tool_call_count,
                "errors": e.error_count,
                "tokens": e.token_usage,
                "estimated_cost_usd": e.estimated_cost,
                "cost_complete": e.cost_complete,
                "error": e.error,
            }
            for e in executions
        ],
        "incidents": [
            {
                "id": i.id,
                "execution_id": i.execution_id,
                "agent": names.get(i.agent_id, i.agent_id),
                "category": i.category,
                "severity": i.severity,
                "title": i.title,
                "status": i.status,
                "evidence": i.evidence,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in incidents
        ],
        "interventions": [
            {
                "id": iv.id,
                "execution_id": iv.execution_id,
                "action": iv.action,
                "status": iv.status,
                "requested_by": iv.requested_by,
                "reason": iv.reason,
                "applied_at": iv.applied_at.isoformat() if iv.applied_at else None,
            }
            for iv in interventions
        ],
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: CurrentUser, session: DbSession) -> ChatResponse:
    digest = await build_workspace_digest(
        session, user.id, agent_id=body.agent_id, execution_id=body.execution_id
    )
    counts = {
        "agents": len(digest["agents"]),
        "executions": len(digest["executions"]),
        "incidents": len(digest["incidents"]),
        "interventions": len(digest["interventions"]),
        "window_days": LOOKBACK_DAYS,
    }

    if not provider_configured():
        return ChatResponse(
            answer=(
                "No supervisor LLM provider is configured, so I cannot answer in "
                "natural language. The deterministic data this question would be "
                "answered from is included below and is also visible on the "
                "Incidents and Monitor pages."
            ),
            grounded_in=counts,
            evaluator=None,
            unavailable_reason=(
                "Set SUPERVISOR_LLM_PROVIDER and SUPERVISOR_LLM_API_KEY in .env to "
                "enable conversational answers."
            ),
        )

    try:
        provider = get_provider()
    except EvaluatorUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    prompt = (
        "Workspace telemetry (the only facts you may use):\n"
        + json.dumps(digest, indent=2, default=str)
        + f"\n\nOperator question: {body.message}\n\n"
        'Return JSON with keys: answer (string, markdown allowed), '
        "supporting_evidence (array of strings quoting ids or values from the "
        "telemetry), confidence (high|medium|low|none)."
    )

    try:
        raw = await provider.complete(SYSTEM_PROMPT, prompt)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Evaluator call failed: {exc}"
        ) from exc

    parsed = parse_json_response(raw)
    answer = parsed.get("answer") or parsed.get("summary") or raw.strip()
    evidence = parsed.get("supporting_evidence") or []
    if evidence:
        answer += "\n\n**Evidence**\n" + "\n".join(f"- {item}" for item in evidence)

    return ChatResponse(
        answer=answer,
        grounded_in=counts,
        evaluator=f"{provider.name}:{provider.model}",
    )
