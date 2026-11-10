"""HTTP transport: batched event delivery, control polling, acknowledgements.

Delivery properties that matter:

* Events are queued and flushed in batches, so instrumenting a hot loop costs
  the agent one queue append, not one HTTP request.
* Every event carries a client-generated id, so a retried batch is
  de-duplicated by the backend instead of double-counting.
* A flush failure keeps the events queued and retries with backoff. Telemetry
  loss is reported, never silently swallowed.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .errors import TransportError

logger = logging.getLogger("reliability.sdk")

DEFAULT_FLUSH_INTERVAL = 0.25
DEFAULT_BATCH_SIZE = 100
MAX_QUEUE = 10_000
MAX_RETRY_DELAY = 10.0


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Transport:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._queues: dict[str, deque[dict[str, Any]]] = {}
        self._flusher: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        #: Called with (execution_id, control_payload) after every ingest.
        self.on_control: Callable[[str, dict[str, Any]], None] | None = None
        self.dropped_events = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._flusher is None or self._flusher.done():
            self._stop.clear()
            self._flusher = asyncio.create_task(self._flush_loop())

    async def aclose(self) -> None:
        self._stop.set()
        if self._flusher is not None:
            try:
                await asyncio.wait_for(self._flusher, timeout=10.0)
            except (asyncio.TimeoutError, TimeoutError):
                self._flusher.cancel()
        await self.flush_all()
        await self._client.aclose()

    # -- REST --------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TransportError(f"{method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise TransportError(
                f"{method} {path} -> {response.status_code}: {response.text[:400]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def register_agent(
        self, name: str, framework: str, sdk_version: str, description: str | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/agents/register",
            json={
                "name": name,
                "framework": framework,
                "sdk_version": sdk_version,
                "description": description,
            },
        )

    async def start_execution(
        self, agent_id: str, task: str | None, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/executions",
            json={"agent_id": agent_id, "task": task, "metadata": metadata},
        )

    async def poll_control(self, execution_id: str, wait: float) -> dict[str, Any]:
        return await self._request(
            "GET", f"/v1/executions/{execution_id}/control", params={"wait": wait}
        )

    async def acknowledge(
        self, intervention_id: str, status: str, detail: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/interventions/ack",
            json={
                "intervention_id": intervention_id,
                "status": status,
                "detail": detail or {},
            },
        )

    # -- event queue -------------------------------------------------------

    def enqueue(
        self, execution_id: str, seq: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        queue = self._queues.setdefault(execution_id, deque())
        if len(queue) >= MAX_QUEUE:
            # Drop the oldest rather than block the agent. Counted and
            # reported so the loss is visible, never silent.
            queue.popleft()
            self.dropped_events += 1
            logger.warning(
                "reliability: event queue full for %s, dropped oldest event "
                "(%d dropped so far)",
                execution_id,
                self.dropped_events,
            )
        queue.append(
            {
                "client_event_id": uuid.uuid4().hex,
                "seq": seq,
                "event_type": event_type,
                "timestamp": utcnow_iso(),
                "payload": payload,
            }
        )

    def pending(self, execution_id: str | None = None) -> int:
        if execution_id is not None:
            return len(self._queues.get(execution_id, ()))
        return sum(len(q) for q in self._queues.values())

    async def _flush_loop(self) -> None:
        delay = self._flush_interval
        while not self._stop.is_set():
            try:
                sent = await self.flush_all()
                delay = self._flush_interval
                if sent:
                    continue
            except TransportError as exc:
                delay = min(delay * 2, MAX_RETRY_DELAY)
                logger.warning("reliability: flush failed (%s), retrying in %.1fs", exc, delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def flush_all(self) -> int:
        """Send every queued event. Returns how many were accepted."""
        total = 0
        for execution_id in list(self._queues):
            total += await self.flush(execution_id)
        return total

    async def flush(self, execution_id: str) -> int:
        """Send queued events for one execution, oldest first."""
        async with self._lock:
            queue = self._queues.get(execution_id)
            if not queue:
                return 0

            sent = 0
            while queue:
                batch = [queue.popleft() for _ in range(min(self._batch_size, len(queue)))]
                try:
                    result = await self._request(
                        "POST",
                        "/v1/ingest/events",
                        json={"execution_id": execution_id, "events": batch},
                    )
                except TransportError:
                    # Put them back in order; the client ids make a retry safe.
                    queue.extendleft(reversed(batch))
                    raise
                sent += len(batch)
                control = (result or {}).get("control")
                if control and self.on_control:
                    self.on_control(execution_id, control)
            return sent
