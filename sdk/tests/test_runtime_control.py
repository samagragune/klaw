"""Checkpoint semantics - the part that makes control real.

Driven against a fake transport so the behaviour under test is the SDK's own
cooperative enforcement, not the network.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from reliability import ExecutionCancelled, Supervisor, ToolBlocked
from reliability.control import ControlState

pytestmark = pytest.mark.asyncio


class FakeTransport:
    """Stands in for ``reliability.transport.Transport``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.acks: list[tuple[str, str]] = []
        self.polls: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.on_control = None

    def start(self) -> None:
        pass

    async def register_agent(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": "agent-1", "name": kwargs.get("name", "agent")}

    async def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": "exec-1", "task": kwargs.get("task")}

    def enqueue(self, execution_id: str, seq: int, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))

    async def flush(self, execution_id: str) -> int:
        return 0

    async def flush_all(self) -> int:
        return 0

    async def poll_control(self, execution_id: str, wait: float) -> dict[str, Any]:
        return await self.polls.get()

    async def acknowledge(self, intervention_id: str, status: str, detail=None) -> dict:
        self.acks.append((intervention_id, status))
        return {}

    async def aclose(self) -> None:
        pass

    # test helpers
    def push(self, payload: dict[str, Any]) -> None:
        self.polls.put_nowait(payload)

    def types(self) -> list[str]:
        return [name for name, _ in self.events]


def state_payload(desired="running", commands=(), blocked=()) -> dict[str, Any]:
    return {
        "execution_id": "exec-1",
        "desired_state": desired,
        "commands": list(commands),
        "blocked_tools": list(blocked),
    }


def command(action: str, intervention_id: str, **parameters: Any) -> dict[str, Any]:
    return {
        "intervention_id": intervention_id,
        "action": action,
        "reason": f"test {action}",
        "parameters": parameters,
    }


def build() -> tuple[Supervisor, FakeTransport]:
    supervisor = Supervisor(api_key="test-key", agent_id="test-agent")
    transport = FakeTransport()
    supervisor._transport = transport  # noqa: SLF001 - test seam
    transport.on_control = supervisor._on_control  # noqa: SLF001
    return supervisor, transport


async def until(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


# ---------------------------------------------------------------------------

async def test_clean_run_emits_a_complete_trace():
    supervisor, transport = build()

    @supervisor.monitor(task="smoke")
    async def agent() -> str:
        async with supervisor.tool("search", {"q": "x"}) as call:
            call.set_result(["a"])
        async with supervisor.llm("demo-model") as call:
            call.record_usage(10, 5)
        return "done"

    assert await agent() == "done"
    assert transport.types() == [
        "execution.started",
        "tool.started",
        "tool.completed",
        "llm.requested",
        "llm.completed",
        "execution.completed",
    ]


async def test_tool_arguments_are_fingerprinted_but_not_sent_by_default():
    supervisor, transport = build()

    @supervisor.monitor
    async def agent() -> None:
        async with supervisor.tool("search", {"q": "confidential"}):
            pass

    await agent()
    payload = dict(transport.events)["tool.started"]
    assert "args" not in payload
    assert len(payload["args_fingerprint"]) == 32
    assert payload["args_shape"] == {"keys": ["q"], "count": 1}
    assert "confidential" not in str(payload)


async def test_tool_failure_is_recorded_and_the_exception_still_propagates():
    supervisor, transport = build()

    @supervisor.monitor
    async def agent() -> None:
        async with supervisor.tool("api"):
            raise RuntimeError("503 from upstream")

    with pytest.raises(RuntimeError):
        await agent()
    assert "tool.failed" in transport.types()
    assert "execution.failed" in transport.types()
    failure = dict(transport.events)["tool.failed"]
    assert failure["error_type"] == "RuntimeError"
    assert "503" in failure["error"]


async def test_cancel_stops_the_agent_and_is_reported_as_applied():
    supervisor, transport = build()
    transport.push(state_payload("cancelled", [command("cancel", "iv-cancel")]))

    @supervisor.monitor
    async def agent() -> str:
        for i in range(200):
            await supervisor.checkpoint(f"step-{i}")
            await asyncio.sleep(0.05)
        return "never gets here"

    with pytest.raises(ExecutionCancelled):
        await agent()

    assert "execution.cancelled" in transport.types()
    assert "execution.completed" not in transport.types()
    # Applied only once, and only after the runtime actually stopped.
    assert transport.acks.count(("iv-cancel", "applied")) == 1


async def test_pause_suspends_work_and_resume_releases_it():
    supervisor, transport = build()
    progress = {"steps": 0}

    @supervisor.monitor
    async def agent() -> str:
        for _ in range(500):
            await supervisor.checkpoint("loop")
            progress["steps"] += 1
            await asyncio.sleep(0.02)
        return "finished"

    task = asyncio.create_task(agent())
    assert await until(lambda: progress["steps"] > 0)

    transport.push(state_payload("paused", [command("pause", "iv-pause")]))
    assert await until(lambda: "execution.paused" in transport.types())

    # The agent must genuinely stop advancing while paused.
    frozen = progress["steps"]
    await asyncio.sleep(0.4)
    assert progress["steps"] == frozen
    assert not task.done()
    assert ("iv-pause", "applied") in transport.acks

    transport.push(state_payload("running", [command("resume", "iv-resume")]))
    assert await until(lambda: progress["steps"] > frozen)
    assert "execution.resumed" in transport.types()
    assert await until(lambda: ("iv-resume", "applied") in transport.acks)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancel_arriving_during_a_pause_is_honoured():
    supervisor, transport = build()

    @supervisor.monitor
    async def agent() -> str:
        for _ in range(500):
            await supervisor.checkpoint("loop")
            await asyncio.sleep(0.02)
        return "finished"

    task = asyncio.create_task(agent())
    transport.push(state_payload("paused", [command("pause", "iv-pause")]))
    assert await until(lambda: "execution.paused" in transport.types())

    transport.push(state_payload("cancelled", [command("cancel", "iv-cancel")]))
    with pytest.raises(ExecutionCancelled):
        await task
    assert ("iv-cancel", "applied") in transport.acks


async def test_blocked_tool_is_refused_and_the_block_is_acknowledged():
    supervisor, transport = build()
    transport.push(
        state_payload(
            "running",
            [command("block_tool", "iv-block", tool_name="delete_everything")],
            blocked=["delete_everything"],
        )
    )

    @supervisor.monitor
    async def agent() -> str:
        # Wait for the control state to arrive, then attempt the tool.
        for _ in range(200):
            if "delete_everything" in supervisor._contexts["exec-1"].control.blocked_tools:
                break
            await asyncio.sleep(0.02)
        async with supervisor.tool("delete_everything", {"path": "/"}):
            return "ran anyway"
        return "unreachable"

    with pytest.raises(ToolBlocked):
        await agent()
    assert "tool.blocked" in transport.types()
    assert "tool.started" not in transport.types()
    assert ("iv-block", "applied") in transport.acks


async def test_commands_are_acknowledged_on_receipt_before_being_applied():
    supervisor, transport = build()

    @supervisor.monitor
    async def agent() -> str:
        for _ in range(500):
            await supervisor.checkpoint("loop")
            await asyncio.sleep(0.02)
        return "finished"

    task = asyncio.create_task(agent())
    transport.push(state_payload("paused", [command("pause", "iv-pause")]))
    assert await until(lambda: ("iv-pause", "acknowledged") in transport.acks)
    assert await until(lambda: ("iv-pause", "applied") in transport.acks)
    # Receipt is reported before enforcement, never the other way round.
    assert transport.acks.index(("iv-pause", "acknowledged")) < transport.acks.index(
        ("iv-pause", "applied")
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_tool_outside_a_monitored_execution_is_a_clear_error():
    supervisor, _ = build()
    with pytest.raises(Exception) as excinfo:
        async with supervisor.tool("search"):
            pass
    assert "monitor" in str(excinfo.value)


async def test_sync_entrypoints_are_rejected_with_an_explanation():
    supervisor, _ = build()
    with pytest.raises(Exception) as excinfo:

        @supervisor.monitor
        def blocking_agent() -> str:
            return "nope"

    assert "async" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ControlState folding
# ---------------------------------------------------------------------------

async def test_control_state_tracks_commands_once():
    state = ControlState()
    first = state.update(state_payload("paused", [command("pause", "iv-1")]))
    assert len(first) == 1
    again = state.update(state_payload("paused", [command("pause", "iv-1")]))
    assert again == []
    assert state.mark_applied("iv-1") is True
    assert state.mark_applied("iv-1") is False
    assert state.pending_apply("pause") == []
