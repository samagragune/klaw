"""Keep a free-tier host awake.

Render's free web services sleep after ~15 minutes without inbound traffic,
and the first request after that waits 30-60 s for a cold start. During that
window adapters fail open, so tool calls are not supervised. A supervisor
that dozes off is not much of a supervisor.

While the process is up, this task requests the service's *public* URL every
``KEEPALIVE_INTERVAL_SECONDS``. Going out through the host's edge and back in
counts as inbound traffic, so the idle timer never fires.

A process cannot wake itself once asleep; on such hosts pair this with any
external uptime pinger. Off unless a public URL is configured.

Enabled when a public URL is known: ``KEEPALIVE_URL``, or Render's own
``RENDER_EXTERNAL_URL`` (set automatically on Render). Off locally.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class KeepaliveStatus:
    """Last outcome, exposed on /health so the dashboard and pingers can see it."""

    url: str | None = None
    interval: float = 0
    last_ok_at: float | None = None
    last_error: str | None = None
    pings: int = 0


status = KeepaliveStatus()


def keepalive_url() -> str | None:
    settings = get_settings()
    url = settings.keepalive_url or os.environ.get("RENDER_EXTERNAL_URL") or ""
    return url.rstrip("/") or None


async def ping_once(client: httpx.AsyncClient, url: str) -> bool:
    try:
        response = await client.get(f"{url}/health", params={"source": "keepalive"})
        ok = response.status_code == 200
    except httpx.HTTPError as exc:
        status.last_error = f"{type(exc).__name__}: {exc}"[:200]
        return False
    status.pings += 1
    if ok:
        status.last_ok_at = time.time()
        status.last_error = None
    else:
        status.last_error = f"HTTP {response.status_code}"
    return ok


async def run_keepalive(stop: asyncio.Event) -> None:
    url = keepalive_url()
    interval = get_settings().keepalive_interval_seconds
    if not url or interval <= 0:
        logger.info("keepalive off (no public URL configured)")
        return
    status.url, status.interval = url, interval
    logger.info("keepalive pinging %s/health every %ss", url, interval)
    async with httpx.AsyncClient(timeout=20) as client:
        # First ping soon after start-up so a fresh deploy is confirmed.
        delay = min(30.0, interval)
        while not stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
            if stop.is_set():
                break
            if not await ping_once(client, url):
                logger.warning("keepalive ping failed: %s", status.last_error)
            delay = interval
