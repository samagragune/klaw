"""Periodic re-evaluation and reaping of in-flight executions.

Detection normally runs when events arrive. Two cases need a background
sweep instead:

* An agent that **hangs** stops sending events - which is exactly when a
  timeout must still be detected.
* An agent whose **process dies** never reports a terminal state, so its
  execution would otherwise sit at "running" forever, inflating every metric
  derived from it.

The second case is handled by reaping: if nothing has been heard from a
runtime for ``stale_execution_seconds`` - no events *and* no control polls -
the execution is closed as failed with an explicit reason saying exactly
that. The record never pretends the runtime confirmed anything.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.bus import get_bus, user_channel
from app.config import get_settings
from app.control import expire_pending_interventions
from app.db import SessionLocal
from app.models import Execution, ExecutionStatus, utcnow
from app.supervision.engine import evaluate_execution

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 10.0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def last_contact(execution: Execution) -> datetime:
    """The most recent moment this runtime proved it was alive."""
    candidates = [
        _aware(execution.last_heartbeat_at),
        _aware(execution.last_event_at),
        _aware(execution.started_at),
    ]
    return max(stamp for stamp in candidates if stamp is not None)


async def reap_if_stale(session, execution: Execution, stale_after: float) -> bool:
    """Close an execution whose runtime has gone silent. Returns True if reaped."""
    silent_for = (datetime.now(timezone.utc) - last_contact(execution)).total_seconds()
    if silent_for < stale_after:
        return False

    execution.status = ExecutionStatus.FAILED
    execution.completed_at = utcnow()
    execution.error = (
        f"No telemetry or control poll received for {silent_for:.0f}s. Reliability "
        "closed this execution as failed because the agent process stopped "
        "reporting - the runtime never confirmed a terminal state itself."
    )
    await expire_pending_interventions(session, execution)
    logger.info("reaped stale execution %s after %.0fs of silence", execution.id, silent_for)

    with contextlib.suppress(Exception):
        await get_bus().publish(
            user_channel(execution.user_id),
            {
                "type": "execution.updated",
                "execution": {
                    "id": execution.id,
                    "agent_id": execution.agent_id,
                    "status": execution.status,
                    "task": execution.task,
                    "event_count": execution.event_count,
                    "tool_call_count": execution.tool_call_count,
                    "error_count": execution.error_count,
                    "token_usage": execution.token_usage,
                    "estimated_cost": execution.estimated_cost,
                    "started_at": execution.started_at.isoformat(),
                    "completed_at": execution.completed_at.isoformat(),
                },
                "events": [],
            },
        )
    return True


async def sweep_once() -> tuple[int, int]:
    """Evaluate every live execution. Returns ``(examined, reaped)``."""
    stale_after = get_settings().stale_execution_seconds
    reaped = 0
    async with SessionLocal() as session:
        executions = list(
            (
                await session.execute(
                    select(Execution).where(
                        Execution.status.in_(
                            (ExecutionStatus.RUNNING, ExecutionStatus.PAUSED)
                        )
                    )
                )
            ).scalars()
        )
        for execution in executions:
            try:
                if await reap_if_stale(session, execution, stale_after):
                    reaped += 1
                    continue
                await evaluate_execution(session, execution)
            except Exception:
                logger.exception("sweep failed for execution %s", execution.id)
                await session.rollback()
        await session.commit()
        return len(executions), reaped


async def run_sweeper(stop: asyncio.Event) -> None:
    logger.info("supervision sweeper started (every %ss)", SWEEP_INTERVAL_SECONDS)
    while not stop.is_set():
        try:
            await sweep_once()
        except Exception:  # pragma: no cover - keep the loop alive
            logger.exception("supervision sweep raised")
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=SWEEP_INTERVAL_SECONDS)
    logger.info("supervision sweeper stopped")
