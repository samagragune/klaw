"""Reliability backend entrypoint."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bus import close_bus, get_bus
from app.config import get_settings
from app.db import init_models
from app.routers import (
    agents,
    analytics,
    approvals,
    auth,
    control,
    executions,
    incidents,
    integrations,
    metrics,
    policies,
    protocol,
    supervisor,
    ws,
)
from app.sweeper import run_sweeper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("reliability")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.reliability_env != "development" and (
        settings.secret_key.startswith("change-me") or len(settings.secret_key) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY must be set to a long random value outside development: "
            "it signs dashboard sessions."
        )
    await init_models()
    get_bus()

    stop = asyncio.Event()
    sweeper = asyncio.create_task(run_sweeper(stop))
    logger.info(
        "reliability backend ready | db=%s bus=%s evaluator=%s",
        "sqlite" if settings.is_sqlite else "postgres",
        settings.event_bus,
        settings.supervisor_llm_provider,
    )
    try:
        yield
    finally:
        stop.set()
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await close_bus()


app = FastAPI(
    title="Reliability",
    description="Control plane for autonomous AI agents.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(executions.router)
app.include_router(control.router)
app.include_router(incidents.router)
app.include_router(policies.router)
app.include_router(metrics.router)
app.include_router(supervisor.router)
app.include_router(ws.router)
app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(protocol.router)
app.include_router(approvals.router)
app.include_router(analytics.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "reliability-backend", "version": "0.1.0"}
