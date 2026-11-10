"""Periodic re-evaluation of in-flight executions.

Detection normally runs when events arrive. An agent that hangs stops sending
events, which is exactly the case where a timeout must still be detected - so
a background sweep re-runs the detectors against every running or paused
execution on a fixed interval.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Execution, ExecutionStatus
from app.supervision.engine import evaluate_execution

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 10.0


async def sweep_once() -> int:
    """Evaluate every live execution. Returns how many were examined."""
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
                await evaluate_execution(session, execution)
            except Exception:
                logger.exception("sweep failed for execution %s", execution.id)
                await session.rollback()
        await session.commit()
        return len(executions)


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
