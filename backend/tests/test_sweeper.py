"""Reaping executions whose runtime went silent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Execution, ExecutionStatus, Intervention, InterventionStatus
from app.sweeper import last_contact, reap_if_stale, sweep_once

pytestmark = pytest.mark.asyncio


async def _make_execution(client, *, started_minutes_ago: float = 0.0) -> str:
    agent = (
        await client.post("/v1/agents/register", json={"name": "reaper-agent"})
    ).json()
    execution_id = (
        await client.post("/v1/executions", json={"agent_id": agent["id"], "task": "t"})
    ).json()["id"]

    if started_minutes_ago:
        async with SessionLocal() as session:
            row = (
                await session.execute(select(Execution).where(Execution.id == execution_id))
            ).scalar_one()
            row.started_at = datetime.now(timezone.utc) - timedelta(
                minutes=started_minutes_ago
            )
            row.last_heartbeat_at = None
            row.last_event_at = None
            await session.commit()
    return execution_id


async def test_a_fresh_execution_is_not_reaped(client):
    execution_id = await _make_execution(client)
    await sweep_once()
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["status"] == "running"


async def test_a_silent_execution_is_closed_with_an_honest_reason(client):
    execution_id = await _make_execution(client, started_minutes_ago=30)
    examined, reaped = await sweep_once()
    assert examined >= 1 and reaped == 1

    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["status"] == "failed"
    assert detail["completed_at"] is not None
    # The record says who concluded this and why; it never implies the
    # runtime reported a terminal state.
    assert "stopped reporting" in detail["error"]
    assert "never confirmed" in detail["error"]


async def test_reaping_expires_commands_that_can_never_be_applied(client):
    execution_id = await _make_execution(client, started_minutes_ago=30)
    pause = (
        await client.post(
            f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
        )
    ).json()

    await sweep_once()

    async with SessionLocal() as session:
        row = (
            await session.execute(select(Intervention).where(Intervention.id == pause["id"]))
        ).scalar_one()
        assert row.status == InterventionStatus.EXPIRED
        assert row.applied_at is None


async def test_a_control_poll_counts_as_proof_of_life(client):
    """A paused agent emits no events; polling is how it stays alive."""
    execution_id = await _make_execution(client, started_minutes_ago=30)
    await client.get(f"/v1/executions/{execution_id}/control")

    _, reaped = await sweep_once()
    assert reaped == 0
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["status"] == "running"


async def test_last_contact_prefers_the_most_recent_signal():
    now = datetime.now(timezone.utc)
    execution = Execution(
        id="x",
        agent_id="a",
        user_id="u",
        status=ExecutionStatus.RUNNING,
        started_at=now - timedelta(minutes=10),
        last_event_at=now - timedelta(minutes=5),
        last_heartbeat_at=now - timedelta(minutes=1),
    )
    assert last_contact(execution) == execution.last_heartbeat_at


async def test_naive_timestamps_do_not_break_reaping():
    """SQLite returns naive datetimes; the comparison must still work."""
    execution = Execution(
        id="x",
        agent_id="a",
        user_id="u",
        status=ExecutionStatus.RUNNING,
        started_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).replace(
            tzinfo=None
        ),
    )
    assert last_contact(execution).tzinfo is not None


async def test_reap_threshold_is_respected():
    execution = Execution(
        id="x",
        agent_id="a",
        user_id="u",
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )

    class _NullSession:
        def add(self, *_args, **_kwargs):
            pass

    assert await reap_if_stale(_NullSession(), execution, stale_after=60) is False
