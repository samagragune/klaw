"""Thread-safe client for the universal event protocol.

Used by framework adapters (OpenAI Agents SDK today). Unlike the original
``Supervisor`` it owns no event loop: events are queued and delivered by a
daemon thread, and the blocking calls (decisions, approvals, control polls)
are plain synchronous HTTP that async adapters run via ``asyncio.to_thread``.
That makes it safe to use from any loop, any thread, and across repeated
``asyncio.run`` calls - which is how real applications drive agent runs.

Failure policy: telemetry never breaks the agent. Delivery failures keep
events queued (bounded) and retry; decision failures are reported to the
caller, which fails open.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("reliability.protocol")

MAX_QUEUE = 10_000
BATCH = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class ProtocolClient:
    def __init__(
        self,
        *,
        platform: str,
        api_url: str | None = None,
        key: str | None = None,
        flush_interval: float = 0.3,
        timeout: float = 10.0,
    ) -> None:
        self.platform = platform
        self.api_url = (api_url or os.environ.get("RELIABILITY_API_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.key = key or os.environ.get("RELIABILITY_INTEGRATION_KEY") or os.environ.get("RELIABILITY_API_KEY") or ""
        self.enabled = bool(self.key)
        if not self.enabled:
            logger.warning(
                "reliability: no RELIABILITY_INTEGRATION_KEY set - supervision is OFF; "
                "the agent runs unsupervised"
            )
        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=timeout,
        )
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._flush_interval = flush_interval
        #: Latest control state per external session id.
        self.control: dict[str, dict[str, Any]] = {}
        #: external session id -> backend execution id
        self.execution_ids: dict[str, str] = {}
        self.dropped = 0
        self._thread = threading.Thread(target=self._loop, name="reliability-protocol", daemon=True)
        if self.enabled:
            self._thread.start()
            atexit.register(self.close)

    # -- delivery ------------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._queue) >= MAX_QUEUE:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(event)
        self._wake.set()

    def _take(self) -> list[dict[str, Any]]:
        with self._lock:
            batch = [self._queue.popleft() for _ in range(min(BATCH, len(self._queue)))]
        return batch

    def _send(self, batch: list[dict[str, Any]]) -> bool:
        try:
            response = self._http.post("/v1/events", json={"events": batch})
        except httpx.HTTPError as exc:
            logger.warning("reliability: delivery failed (%s); will retry", exc)
            return False
        if response.status_code >= 500:
            return False
        if response.status_code >= 400:
            # A rejected batch will be rejected again; drop it loudly.
            logger.error("reliability: batch rejected %s: %s", response.status_code, response.text[:300])
            return True
        for state in response.json().get("sessions", []):
            self.control[state["session_id"]] = state
            self.execution_ids[state["session_id"]] = state["execution_id"]
        return True

    def flush(self) -> None:
        while True:
            batch = self._take()
            if not batch:
                return
            if not self._send(batch):
                with self._lock:
                    self._queue.extendleft(reversed(batch))
                return

    def _loop(self) -> None:
        delay = self._flush_interval
        while not self._closed:
            self._wake.wait(delay)
            self._wake.clear()
            before = len(self._queue)
            self.flush()
            delay = self._flush_interval if len(self._queue) < before or not self._queue else min(delay * 2, 10)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake.set()
        try:
            self.flush()
        finally:
            self._http.close()

    # -- synchronous calls ---------------------------------------------------

    def connect(self, capabilities: list[str], details: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            response = self._http.post(
                "/v1/integrations/connect", json={"capabilities": capabilities, "details": details}
            )
            return response.json() if response.status_code < 400 else None
        except httpx.HTTPError:
            return None

    def decide(self, event: dict[str, Any], timeout: float = 15.0) -> dict[str, Any] | None:
        """Ask for a decision. ``None`` means the supervisor could not answer."""
        if not self.enabled:
            return None
        self.flush()  # earlier events must land first so the decision sees them
        try:
            response = self._http.post("/v1/decisions", json={"event": event}, timeout=timeout)
        except httpx.HTTPError as exc:
            logger.warning("reliability: decision unavailable (%s); failing open", exc)
            return None
        if response.status_code >= 400:
            logger.warning("reliability: decision refused %s: %s", response.status_code, response.text[:200])
            return None
        decision = response.json()
        self.execution_ids[event["session_id"]] = decision["execution_id"]
        return decision

    def wait_approval(self, approval_id: str, deadline: float) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "pending"}
        while time.time() < deadline:
            try:
                response = self._http.get(
                    f"/v1/approvals/{approval_id}/wait", params={"wait": 20}, timeout=30
                )
                result = response.json()
            except httpx.HTTPError:
                time.sleep(1)
                continue
            if result.get("status") != "pending":
                return result
        return result

    def poll_control(self, session_id: str, wait: float = 0) -> dict[str, Any] | None:
        execution_id = self.execution_ids.get(session_id)
        if not execution_id or not self.enabled:
            return None
        try:
            response = self._http.get(
                f"/v1/executions/{execution_id}/control", params={"wait": wait}, timeout=wait + 10
            )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        return response.json()

    def ack(self, intervention_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
        try:
            self._http.post(
                "/v1/interventions/ack",
                json={"intervention_id": intervention_id, "status": status, "detail": detail or {}},
            )
        except httpx.HTTPError as exc:
            logger.warning("reliability: ack failed: %s", exc)
