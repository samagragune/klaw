"""Policy configuration.

A policy with ``agent_id = NULL`` is the user-wide default; an agent-scoped
policy overrides it wholesale. ``/effective`` returns what the supervision
engine will actually apply, so the dashboard never has to reimplement the
resolution rule.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.models import Policy
from app.routers.agents import get_owned_agent
from app.schemas import PolicyOut, PolicyUpsert
from app.security import CurrentUser, DbSession, record_audit
from app.supervision.engine import DEFAULT_CONFIG, DEFAULT_MODE, effective_policy

router = APIRouter(prefix="/v1/policies", tags=["policies"])


@router.get("", response_model=list[PolicyOut])
async def list_policies(user: CurrentUser, session: DbSession) -> list[Policy]:
    result = await session.execute(
        select(Policy).where(Policy.user_id == user.id).order_by(Policy.created_at)
    )
    return list(result.scalars())


@router.get("/defaults")
async def policy_defaults() -> dict[str, Any]:
    """Built-in limits applied when no policy row exists."""
    return {"supervision_mode": DEFAULT_MODE, "configuration": DEFAULT_CONFIG}


@router.get("/effective")
async def get_effective_policy(
    user: CurrentUser,
    session: DbSession,
    agent_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    if agent_id:
        await get_owned_agent(session, user, agent_id)
    mode, config, policy = await effective_policy(
        session, user_id=user.id, agent_id=agent_id or ""
    )
    return {
        "agent_id": agent_id,
        "supervision_mode": mode,
        "configuration": config,
        "source": (
            "agent" if policy and policy.agent_id else "user_default" if policy else "built_in"
        ),
        "policy_id": policy.id if policy else None,
    }


@router.put("", response_model=PolicyOut)
async def upsert_policy(
    body: PolicyUpsert, user: CurrentUser, session: DbSession
) -> Policy:
    if body.agent_id:
        await get_owned_agent(session, user, body.agent_id)

    query = select(Policy).where(Policy.user_id == user.id)
    query = (
        query.where(Policy.agent_id == body.agent_id)
        if body.agent_id
        else query.where(Policy.agent_id.is_(None))
    )
    policy = (await session.execute(query)).scalar_one_or_none()

    configuration = body.configuration.model_dump()
    if policy is None:
        policy = Policy(
            user_id=user.id,
            agent_id=body.agent_id,
            name=body.name,
            supervision_mode=body.supervision_mode,
            configuration=configuration,
        )
        session.add(policy)
        await session.flush()
    else:
        policy.name = body.name
        policy.supervision_mode = body.supervision_mode
        policy.configuration = configuration

    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="policy.updated",
        target_type="policy",
        target_id=policy.id,
        detail={
            "agent_id": body.agent_id,
            "supervision_mode": body.supervision_mode,
            "configuration": configuration,
        },
    )
    await session.commit()
    return policy


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: str, user: CurrentUser, session: DbSession) -> None:
    policy = (
        await session.execute(
            select(Policy).where(Policy.id == policy_id).where(Policy.user_id == user.id)
        )
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    await session.delete(policy)
    await record_audit(
        session,
        user_id=user.id,
        actor="user",
        action="policy.deleted",
        target_type="policy",
        target_id=policy_id,
    )
    await session.commit()
