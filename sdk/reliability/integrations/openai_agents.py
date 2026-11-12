"""OpenAI Agents SDK adapter.

    from reliability.integrations.openai_agents import AgentsSupervisor

    supervisor = AgentsSupervisor(app_name="support-bot")       # reads env
    result = await supervisor.run(triage_agent, "My seat is wrong")

or, keeping your own ``Runner`` call:

    supervisor.instrument(triage_agent)                         # tool guardrails
    result = await Runner.run(triage_agent, text, hooks=supervisor.hooks())

What each mechanism is used for - and what it is not claimed to do:

* **Run hooks** (``RunHooks``) - awaited by the runner before every model call
  and tool call. These are the control boundaries: pause (the coroutine waits
  until resumed) and stop (the run is aborted) take effect here. A model call
  already in flight is not interrupted.
* **Tool input guardrails** - attached to every function tool by
  ``instrument``. This is the decision point: the supervisor can allow,
  block with a message the model sees, or hold the call for human approval.
* **Tracing processor** - observation only: guardrail results and errors
  from spans. Tracing gives no control and none is claimed.

Hosted tools (web search, file search, code interpreter) run on OpenAI's side;
they are observed, not intercepted. Only applications built on the OpenAI
Agents SDK are supported - not every application that calls OpenAI models.

Configuration: ``RELIABILITY_API_URL`` and ``RELIABILITY_INTEGRATION_KEY``
(an ``rl_int_...`` key for an ``openai_agents`` integration).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
import socket
import time
from contextvars import ContextVar
from typing import Any

from agents import (
    Agent,
    AgentHooks,  # noqa: F401  (re-exported for users composing hooks)
    RunHooks,
    Runner,
    ToolGuardrailFunctionOutput,
    add_trace_processor,
    tool_input_guardrail,
)
from agents.tool import FunctionTool
from agents.tracing import TracingProcessor, get_current_trace

from ..protocol_client import ProtocolClient, new_id, now_iso

logger = logging.getLogger("reliability.openai_agents")

CAPABILITIES = [
    "observe",
    "block_tool",
    "request_approval",
    "corrective_feedback",
    "stop",
    "pause",
    "runtime_control",
]
TAG = "[Reliability supervisor]"
MAX_TEXT = 4000

_run_session: ContextVar[str | None] = ContextVar("reliability_openai_session", default=None)


class RunStopped(RuntimeError):
    """Raised inside a run when an operator or the supervisor stops it."""


def _clip(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


def _fp(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    try:  # tool arguments arrive as a JSON string; canonicalise key order
        text = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _model_name(agent: Agent) -> str | None:
    model = getattr(agent, "model", None)
    if isinstance(model, str):
        return model
    return getattr(model, "model", None) or (type(model).__name__ if model else None)


def _input_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        for item in reversed(value):
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, str) and item.get("role") == "user":
                return content
    except TypeError:
        pass
    return _clip(value) or ""


class AgentsSupervisor:
    def __init__(
        self,
        *,
        app_name: str = "openai-agents-app",
        api_url: str | None = None,
        integration_key: str | None = None,
        capture_inputs: bool = True,
        capture_outputs: bool = True,
        approval_wait_seconds: float = 300,
        install_tracing: bool = True,
    ) -> None:
        self.app_name = app_name
        self.capture_inputs = capture_inputs
        self.capture_outputs = capture_outputs
        self.approval_wait_seconds = approval_wait_seconds
        self.client = ProtocolClient(platform="openai_agents", api_url=api_url, key=integration_key)
        self.client.connect(
            CAPABILITIES,
            {
                "app": app_name,
                "host": socket.gethostname(),
                "os": f"{platform.system()} {platform.release()}",
                "adapter": "reliability.integrations.openai_agents",
            },
        )
        self._root_agent: dict[str, str] = {}
        self._actor: dict[str, str] = {}
        self._decided_calls: set[str] = set()
        self._call_ids: dict[tuple[str, str], list[str]] = {}
        if install_tracing:
            add_trace_processor(_TracingBridge(self))

    # -- event helpers -------------------------------------------------------

    def _session(self) -> str:
        session = _run_session.get()
        if session:
            return session
        trace = get_current_trace()
        return trace.trace_id if trace is not None else "untracked"

    def _event(self, event_type: str, payload: dict[str, Any], *, session: str | None = None,
               actor: str | None = None) -> dict[str, Any]:
        session = session or self._session()
        root = self._root_agent.get(session, "agent")
        actor = actor or self._actor.get(session)
        return {
            "event_id": new_id(),
            "platform": "openai_agents",
            "agent_id": f"{self.app_name}:{root}",
            "agent_name": f"{self.app_name} · {root}",
            "session_id": session,
            "event_type": event_type,
            "timestamp": now_iso(),
            "task_id": session,
            "execution_context": {"actor": actor} if actor and actor != root else {},
            "payload": {k: v for k, v in payload.items() if v is not None},
        }

    def emit(self, event_type: str, payload: dict[str, Any], **kw: Any) -> None:
        self.client.emit(self._event(event_type, payload, **kw))

    # -- control boundary ----------------------------------------------------

    async def checkpoint(self, label: str) -> None:
        """Honour pause and stop. Called by the run hooks at every boundary."""
        session = self._session()
        state = await asyncio.to_thread(self.client.poll_control, session, 0)
        if not state:
            return
        if state["desired_state"] == "cancelled":
            await self._apply(state, "cancel", label)
            reason = next((c.get("reason") for c in state.get("commands", []) if c.get("reason")), None)
            raise RunStopped(reason or "Stopped by an operator")
        if state["desired_state"] == "paused":
            await self._apply(state, "pause", label)
            self.emit("SESSION_PAUSED", {"checkpoint": label})
            await asyncio.to_thread(self.client.flush)
            while state and state["desired_state"] == "paused":
                state = await asyncio.to_thread(self.client.poll_control, session, 20)
            self.emit("SESSION_RESUMED", {"checkpoint": label})
            if state:
                await self._apply(state, "resume", label)
                if state["desired_state"] == "cancelled":
                    await self._apply(state, "cancel", label)
                    raise RunStopped("Stopped by an operator while paused")

    async def _apply(self, state: dict[str, Any], action: str, label: str) -> None:
        for command in state.get("commands", []):
            if command["action"] == action:
                await asyncio.to_thread(
                    self.client.ack, command["intervention_id"], "applied", {"checkpoint": label}
                )

    # -- decision point (tool guardrail) --------------------------------------

    async def decide_tool(self, tool_name: str, call_id: str, arguments: Any) -> tuple[bool, str | None]:
        """Returns ``(allowed, message_for_model)``."""
        session = self._session()
        self._decided_calls.add(call_id)
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            parsed = arguments
        event = self._event("TOOL_CALL_STARTED", {
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "args_fingerprint": _fp(arguments),
            "tool_input": parsed if self.capture_inputs else None,
        })
        decision = await asyncio.to_thread(self.client.decide, event)
        if decision is None:
            return True, None  # supervisor unavailable: fail open
        action = decision["action"]
        message = f"{TAG} {decision.get('feedback') or decision['reason']}"
        if action in ("ALLOW", "WARN"):
            return True, None
        if action == "STOP":
            raise RunStopped(decision["reason"])
        if action == "REQUEST_APPROVAL" and decision.get("approval"):
            ticket = decision["approval"]
            result = await asyncio.to_thread(
                self.client.wait_approval, ticket["id"], time.time() + self.approval_wait_seconds
            )
            status = result.get("status")
            if status == "approved":
                return True, None
            who = result.get("decided_by") or "nobody"
            note = f": {result['note']}" if result.get("note") else ""
            return False, f"{TAG} Not approved ({status} by {who}{note}). Do not retry this action."
        return False, message

    # -- public API --------------------------------------------------------------

    def hooks(self) -> RunHooks:
        return _Hooks(self)

    def instrument(self, agent: Agent, _seen: set[int] | None = None) -> Agent:
        """Attach the supervisor's guardrail to every function tool, recursively
        through handoffs and agents-as-tools. Idempotent."""
        seen = _seen if _seen is not None else set()
        if id(agent) in seen:
            return agent
        seen.add(id(agent))
        guard = _guardrail_for(self)
        for tool in agent.tools:
            if isinstance(tool, FunctionTool):
                existing = list(getattr(tool, "tool_input_guardrails", None) or [])
                if not any(getattr(g, "name", "") == guard.name for g in existing):
                    tool.tool_input_guardrails = existing + [guard]
        for handoff in agent.handoffs:
            target = getattr(handoff, "agent", None) or handoff
            if isinstance(target, Agent):
                self.instrument(target, seen)
        return agent

    async def run(self, agent: Agent, input: Any, *, session_id: str | None = None, **kwargs: Any):
        """``Runner.run`` with supervision: one run = one session with one task."""
        self.instrument(agent)
        session = session_id or f"run-{new_id()}"
        token = _run_session.set(session)
        self._root_agent[session] = agent.name
        self._actor[session] = agent.name
        objective = _input_text(input)
        self.emit("SESSION_STARTED", {"context": {"model": _model_name(agent)}})
        self.emit("TASK_CREATED", {"objective": _clip(objective)})
        hooks = kwargs.pop("hooks", None)
        try:
            result = await Runner.run(agent, input, hooks=_ChainedHooks(self.hooks(), hooks), **kwargs)
        except RunStopped as exc:
            self.emit("AGENT_STOPPED", {"reason": str(exc), "terminal": True})
            await asyncio.to_thread(self.client.flush)
            raise
        except Exception as exc:
            name = type(exc).__name__
            if "Guardrail" in name:
                self.emit("GUARDRAIL_RESULT", {"guardrail": name, "triggered": True, "message": _clip(str(exc), 500)})
            self.emit("AGENT_ERROR", {"error": _clip(str(exc), 1500), "error_type": name})
            self.emit("SESSION_ENDED", {"status": "failed", "error": _clip(f"{name}: {exc}", 1500)})
            await asyncio.to_thread(self.client.flush)
            raise
        else:
            output = _clip(result.final_output) if self.capture_outputs else None
            self.emit("TASK_COMPLETED", {"output": output})
            self.emit("SESSION_ENDED", {"status": "completed", "output": output})
            await asyncio.to_thread(self.client.flush)
            return result
        finally:
            _run_session.reset(token)

    def close(self) -> None:
        self.client.close()


def _guardrail_for(supervisor: AgentsSupervisor):
    @tool_input_guardrail(name="reliability_supervisor")
    async def reliability_supervisor(data) -> ToolGuardrailFunctionOutput:
        ctx = data.context
        allowed, message = await supervisor.decide_tool(
            ctx.tool_name, ctx.tool_call_id, ctx.tool_arguments
        )
        if allowed:
            return ToolGuardrailFunctionOutput.allow()
        supervisor.emit("TOOL_CALL_BLOCKED", {
            "tool_name": ctx.tool_name, "tool_call_id": ctx.tool_call_id, "reason": message,
        })
        return ToolGuardrailFunctionOutput.reject_content(message or f"{TAG} Refused.")

    return reliability_supervisor


class _Hooks(RunHooks):
    """The control boundaries. Every method is awaited by the runner."""

    def __init__(self, supervisor: AgentsSupervisor) -> None:
        self.s = supervisor
        self._llm_started: dict[str, float] = {}

    async def on_agent_start(self, context, agent) -> None:
        self.s._actor[self.s._session()] = agent.name
        await self.s.checkpoint(f"agent:{agent.name}")

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        await self.s.checkpoint(f"llm:{agent.name}")
        self._llm_started[self.s._session()] = time.perf_counter()

    async def on_llm_end(self, context, agent, response) -> None:
        usage = getattr(response, "usage", None)
        started = self._llm_started.pop(self.s._session(), None)
        self.s.emit("MODEL_CALL", {
            "model": _model_name(agent),
            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1) if started else None,
            "status": "ok",
        }, actor=agent.name)

    async def on_tool_start(self, context, agent, tool) -> None:
        await self.s.checkpoint(f"tool:{tool.name}")
        call_id = getattr(context, "tool_call_id", None) or new_id()
        if call_id not in self.s._decided_calls:
            # Not instrumented (hosted tool, or instrument() not used):
            # observed, not decided.
            self.s.emit("TOOL_CALL_STARTED", {
                "tool_name": tool.name,
                "tool_call_id": call_id,
                "args_fingerprint": _fp(getattr(context, "tool_arguments", "")),
                "tool_input": _clip(getattr(context, "tool_arguments", None)) if self.s.capture_inputs else None,
            }, actor=agent.name)

    async def on_tool_end(self, context, agent, tool, result) -> None:
        call_id = getattr(context, "tool_call_id", None)
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        failed = isinstance(result, str) and result.startswith("An error occurred while running the tool")
        self.s.emit("TOOL_CALL_FAILED" if failed else "TOOL_CALL_COMPLETED", {
            "tool_name": tool.name,
            "tool_call_id": call_id,
            "result_fingerprint": _fp(text),
            "result_preview": _clip(text, 1500) if self.s.capture_outputs else None,
            **({"error": _clip(text, 500), "error_type": "tool_error"} if failed else {}),
        }, actor=agent.name)

    async def on_handoff(self, context, from_agent, to_agent) -> None:
        self.s.emit("AGENT_HANDOFF", {"from_agent": from_agent.name, "to_agent": to_agent.name})
        self.s._actor[self.s._session()] = to_agent.name


class _ChainedHooks(RunHooks):
    """Ours first (control), then the application's own hooks."""

    def __init__(self, ours: RunHooks, theirs: RunHooks | None) -> None:
        self.hooks = [h for h in (ours, theirs) if h is not None]

    def __getattribute__(self, name: str):
        if name.startswith("on_"):
            hooks = object.__getattribute__(self, "hooks")

            async def call(*args, **kwargs):
                for h in hooks:
                    await getattr(h, name)(*args, **kwargs)

            return call
        return object.__getattribute__(self, name)


class _TracingBridge(TracingProcessor):
    """Observation from traces: guardrail outcomes and span errors."""

    def __init__(self, supervisor: AgentsSupervisor) -> None:
        self.s = supervisor

    def on_trace_start(self, trace) -> None: ...
    def on_trace_end(self, trace) -> None: ...
    def on_span_start(self, span) -> None: ...

    def on_span_end(self, span) -> None:
        try:
            data = span.span_data
            kind = type(data).__name__
            session = _run_session.get()
            if session is None:
                return
            if kind == "GuardrailSpanData":
                self.s.emit("GUARDRAIL_RESULT", {
                    "guardrail": getattr(data, "name", None),
                    "triggered": bool(getattr(data, "triggered", False)),
                }, session=session)
            elif span.error and kind not in ("FunctionSpanData",):
                self.s.emit("LOG", {
                    "message": f"{kind} error: {_clip(span.error.get('message'), 500)}",
                }, session=session)
        except Exception:  # never let observation break the run
            logger.debug("tracing bridge failed", exc_info=True)

    def shutdown(self) -> None:
        self.s.client.flush()

    def force_flush(self) -> None:
        self.s.client.flush()
