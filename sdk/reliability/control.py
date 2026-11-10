"""Runtime-side control state.

The backend is authoritative about what *should* happen; this object is the
agent process's view of it, refreshed from two sources:

* the response to every event-ingest call (free, no extra round trip), and
* a long poll that parks on the server until a command is issued.

Nothing here stops anything by itself. Enforcement happens at the checkpoints
in ``supervisor.py``, which is the only place that can actually suspend the
agent's own coroutine.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

RUNNING = "running"
PAUSED = "paused"
CANCELLED = "cancelled"


@dataclass
class Command:
    intervention_id: str
    action: str
    reason: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


class ControlState:
    """Mutable view of the control plane's intent for one execution."""

    def __init__(self) -> None:
        self.desired_state: str = RUNNING
        self.blocked_tools: set[str] = set()
        self._commands: dict[str, Command] = {}
        #: Set whenever anything changes, so a paused checkpoint wakes up
        #: immediately rather than on a polling interval.
        self.changed = asyncio.Event()
        #: Commands already reported to the backend as received.
        self._acknowledged: set[str] = set()
        #: Commands already reported as applied.
        self._applied: set[str] = set()

    def update(self, payload: dict[str, Any]) -> list[Command]:
        """Apply a ``ControlStateOut`` payload. Returns newly seen commands."""
        self.desired_state = payload.get("desired_state", RUNNING)
        self.blocked_tools = set(payload.get("blocked_tools") or ())

        new: list[Command] = []
        incoming = payload.get("commands") or []
        for raw in incoming:
            command = Command(
                intervention_id=raw["intervention_id"],
                action=raw["action"],
                reason=raw.get("reason"),
                parameters=raw.get("parameters") or {},
            )
            if command.intervention_id not in self._commands:
                new.append(command)
            self._commands[command.intervention_id] = command

        self.changed.set()
        return new

    def commands_for(self, action: str) -> list[Command]:
        return [c for c in self._commands.values() if c.action == action]

    def pending_apply(self, action: str) -> list[Command]:
        """Commands of ``action`` the runtime has not yet confirmed applying."""
        return [c for c in self.commands_for(action) if c.intervention_id not in self._applied]

    def mark_acknowledged(self, intervention_id: str) -> bool:
        if intervention_id in self._acknowledged:
            return False
        self._acknowledged.add(intervention_id)
        return True

    def mark_applied(self, intervention_id: str) -> bool:
        if intervention_id in self._applied:
            return False
        self._applied.add(intervention_id)
        return True

    def forget(self, intervention_id: str) -> None:
        self._commands.pop(intervention_id, None)

    async def wait_for_change(self, timeout: float) -> bool:
        """Block until the state changes or ``timeout`` elapses."""
        self.changed.clear()
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False
