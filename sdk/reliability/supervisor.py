"""The developer-facing SDK.

    from reliability import Supervisor

    supervisor = Supervisor(api_key="...", agent_id="research-agent")

    @supervisor.monitor
    async def research():
        async with supervisor.tool("search", {"q": "AI startups"}):
            ...
        return "done"

    result = await research()

How control actually works
--------------------------
Observing an agent does not grant the ability to stop it. Reliability gets
that ability because this SDK sits *inside* the agent's own coroutine and
yields at checkpoints:

* entering ``monitor``
* before every ``tool`` call
* before every ``llm`` call
* anywhere the developer calls ``await supervisor.checkpoint()``

At a checkpoint the runtime compares itself against the control plane's
desired state and suspends, aborts or refuses a tool accordingly - and only
then reports back that the command was applied. An agent that never reaches a
checkpoint cannot be paused, and the dashboard will keep showing the command
as pending rather than pretending otherwise.

This module is async-first: ``monitor`` wraps coroutine functions. Synchronous
agents are not supported yet.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable

from . import events as E
from .capture import CaptureConfig, arg_shape, fingerprint, preview, redact
from .control import CANCELLED, PAUSED, ControlState
from .errors import ConfigurationError, ExecutionCancelled, ToolBlocked, TransportError
from .transport import Transport

__version__ = "0.1.0"

logger = logging.getLogger("reliability.sdk")

DEFAULT_API_URL = "http://127.0.0.1:8000"
#: How long a control long poll parks on the server before returning empty.
DEFAULT_POLL_WAIT = 25.0
#: How often a paused checkpoint re-checks, as a backstop if the poller dies.
PAUSE_RECHECK_SECONDS = 1.0


class _ExecutionContext:
    """Per-execution runtime state, carried in a ContextVar."""

    def __init__(self, execution_id: str, transport: Transport) -> None:
        self.execution_id = execution_id
        self.transport = transport
        self.control = ControlState()
        self.seq = 0
        self.paused = False
        self.finished = False
        self.poller: asyncio.Task[None] | None = None

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.transport.enqueue(self.execution_id, self.seq, event_type, payload or {})
        self.seq += 1

    async def flush(self) -> None:
        with contextlib.suppress(TransportError):
            await self.transport.flush(self.execution_id)


_current: ContextVar[_ExecutionContext | None] = ContextVar(
    "reliability_execution", default=None
)


class ToolCall:
    """Handle yielded by ``Supervisor.tool``."""

    def __init__(self, tool_name: str, call_id: str) -> None:
        self.tool_name = tool_name
        self.call_id = call_id
        self.result: Any = None
        self._has_result = False

    def set_result(self, value: Any) -> Any:
        self.result = value
        self._has_result = True
        return value


class LlmCall:
    """Handle yielded by ``Supervisor.llm``.

    Token counts are whatever the caller reports. The SDK never estimates
    them, so a usage figure in the dashboard always traces back to a number
    the provider actually returned.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.completion: str | None = None

    def record_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)

    def set_completion(self, text: str | None) -> None:
        self.completion = text


class Supervisor:
    """Connects one agent to a Reliability backend.

    Parameters
    ----------
    api_key:
        Reliability API key. Falls back to ``RELIABILITY_API_KEY``.
    agent_id:
        The agent's stable name, e.g. ``"research-agent"``. Registration is
        idempotent on this name; the backend's own UUID is resolved for you.
    api_url:
        Backend base URL. Falls back to ``RELIABILITY_API_URL``.
    capture:
        Privacy settings. Defaults to metadata plus error messages - tool
        arguments, results, prompts and completions stay in your process
        unless you opt in.
    """

    def __init__(
        self,
        api_key: str | None = None,
        agent_id: str | None = None,
        *,
        api_url: str | None = None,
        framework: str = "custom",
        description: str | None = None,
        capture: CaptureConfig | None = None,
        poll_wait: float = DEFAULT_POLL_WAIT,
        flush_interval: float = 0.25,
    ) -> None:
        self.api_key = api_key or os.environ.get("RELIABILITY_API_KEY", "")
        self.agent_name = agent_id or os.environ.get("RELIABILITY_AGENT_ID", "")
        self.api_url = api_url or os.environ.get("RELIABILITY_API_URL", DEFAULT_API_URL)
        self.framework = framework
        self.description = description
        self.capture = capture or CaptureConfig()
        self._poll_wait = poll_wait

        if not self.api_key:
            raise ConfigurationError(
                "No API key. Pass api_key=... or set RELIABILITY_API_KEY."
            )
        if not self.agent_name:
            raise ConfigurationError(
                "No agent id. Pass agent_id='my-agent' or set RELIABILITY_AGENT_ID."
            )

        self._transport = Transport(
            self.api_url, self.api_key, flush_interval=flush_interval
        )
        self._transport.on_control = self._on_control
        self._agent: dict[str, Any] | None = None
        self._contexts: dict[str, _ExecutionContext] = {}

    # -- properties --------------------------------------------------------

    @property
    def agent(self) -> dict[str, Any] | None:
        """The registered agent record, once ``register`` has run."""
        return self._agent

    @property
    def execution_id(self) -> str | None:
        ctx = _current.get()
        return ctx.execution_id if ctx else None

    # -- lifecycle ---------------------------------------------------------

    async def register(self) -> dict[str, Any]:
        """Register (or refresh) this agent. Safe to call repeatedly."""
        if self._agent is None:
            self._agent = await self._transport.register_agent(
                name=self.agent_name,
                framework=self.framework,
                sdk_version=__version__,
                description=self.description,
            )
            logger.info(
                "reliability: agent '%s' registered as %s",
                self.agent_name,
                self._agent["id"],
            )
        return self._agent

    async def aclose(self) -> None:
        """Flush telemetry and release connections."""
        await self._transport.aclose()

    async def __aenter__(self) -> "Supervisor":
        await self.register()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- control plane -----------------------------------------------------

    def _on_control(self, execution_id: str, payload: dict[str, Any]) -> None:
        """Called from the transport after every ingest response."""
        ctx = self._contexts.get(execution_id)
        if ctx is None:
            return
        new = ctx.control.update(payload)
        for command in new:
            # Acknowledge receipt out of band; enforcement happens at the
            # next checkpoint and is reported separately.
            asyncio.create_task(self._ack(ctx, command.intervention_id, "acknowledged"))

    async def _ack(
        self,
        ctx: _ExecutionContext,
        intervention_id: str,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if status == "acknowledged" and not ctx.control.mark_acknowledged(intervention_id):
            return
        if status == "applied" and not ctx.control.mark_applied(intervention_id):
            return
        try:
            await self._transport.acknowledge(intervention_id, status, detail)
        except TransportError as exc:
            logger.warning("reliability: could not acknowledge %s: %s", intervention_id, exc)

    async def _poll_loop(self, ctx: _ExecutionContext) -> None:
        backoff = 1.0
        while not ctx.finished:
            try:
                payload = await self._transport.poll_control(
                    ctx.execution_id, wait=self._poll_wait
                )
                backoff = 1.0
            except TransportError as exc:
                logger.debug("reliability: control poll failed: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue
            except asyncio.CancelledError:
                raise
            for command in ctx.control.update(payload):
                await self._ack(ctx, command.intervention_id, "acknowledged")

    # -- checkpoints -------------------------------------------------------

    def _require_context(self) -> _ExecutionContext:
        ctx = _current.get()
        if ctx is None:
            raise ConfigurationError(
                "No active execution. Wrap your entrypoint with @supervisor.monitor "
                "so tool and llm calls belong to a traced execution."
            )
        return ctx

    async def checkpoint(self, label: str | None = None) -> None:
        """Yield to the control plane.

        Suspends while paused, raises ``ExecutionCancelled`` when cancelled,
        and returns immediately otherwise. Call this inside long loops that do
        not go through ``tool`` or ``llm``.
        """
        await self._enforce(self._require_context(), label=label or "manual")

    async def _enforce(
        self,
        ctx: _ExecutionContext,
        *,
        label: str,
        tool_name: str | None = None,
    ) -> None:
        state = ctx.control

        await self._handle_cancel(ctx, label)
        await self._handle_pause(ctx, label)
        # A cancel can arrive while paused; re-check after waking.
        await self._handle_cancel(ctx, label)

        if tool_name and tool_name in state.blocked_tools:
            blocking = [
                c
                for c in state.pending_apply("block_tool")
                if c.parameters.get("tool_name") == tool_name
            ]
            reason = blocking[0].reason if blocking else None
            ctx.emit(
                E.TOOL_BLOCKED,
                {"tool_name": tool_name, "checkpoint": label, "reason": reason},
            )
            await ctx.flush()
            for command in blocking:
                await self._ack(
                    ctx,
                    command.intervention_id,
                    "applied",
                    {"checkpoint": label, "tool_name": tool_name},
                )
            raise ToolBlocked(tool_name, reason)

    async def _handle_cancel(self, ctx: _ExecutionContext, label: str) -> None:
        if ctx.control.desired_state != CANCELLED:
            return
        commands = ctx.control.pending_apply("cancel")
        reason = commands[0].reason if commands else "cancelled by Reliability"
        intervention_id = commands[0].intervention_id if commands else None

        ctx.emit(
            E.EXECUTION_CANCELLED,
            {
                "reason": reason,
                "checkpoint": label,
                "intervention_id": intervention_id,
            },
        )
        ctx.finished = True
        await ctx.flush()
        for command in commands:
            await self._ack(
                ctx, command.intervention_id, "applied", {"checkpoint": label}
            )
        raise ExecutionCancelled(reason, intervention_id)

    async def _handle_pause(self, ctx: _ExecutionContext, label: str) -> None:
        state = ctx.control
        while state.desired_state == PAUSED:
            if not ctx.paused:
                ctx.paused = True
                ctx.emit(E.EXECUTION_PAUSED, {"checkpoint": label})
                await ctx.flush()
                for command in state.pending_apply("pause"):
                    await self._ack(
                        ctx, command.intervention_id, "applied", {"checkpoint": label}
                    )
                logger.info(
                    "reliability: execution %s paused at %s", ctx.execution_id, label
                )
            await state.wait_for_change(PAUSE_RECHECK_SECONDS)

        if ctx.paused:
            ctx.paused = False
            ctx.emit(E.EXECUTION_RESUMED, {"checkpoint": label})
            await ctx.flush()
            for command in state.pending_apply("resume"):
                await self._ack(
                    ctx, command.intervention_id, "applied", {"checkpoint": label}
                )
            logger.info("reliability: execution %s resumed", ctx.execution_id)

    # -- instrumentation ---------------------------------------------------

    def monitor(
        self, func: Callable[..., Any] | None = None, *, task: str | None = None
    ) -> Any:
        """Decorate an async entrypoint so its run becomes a traced execution.

        Usable bare (``@supervisor.monitor``) or with a task description
        (``@supervisor.monitor(task="Research AI startups")``).
        """

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            if not inspect.iscoroutinefunction(fn):
                raise ConfigurationError(
                    f"@supervisor.monitor requires an async function; {fn.__name__} "
                    "is synchronous. Async support is what makes cooperative pause "
                    "and cancellation possible."
                )

            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return await self._run(fn, args, kwargs, task)

            return wrapper

        return decorate if func is None else decorate(func)

    async def _run(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        task: str | None,
    ) -> Any:
        agent = await self.register()
        self._transport.start()

        description = task or fn.__doc__ or fn.__name__
        record = await self._transport.start_execution(
            agent_id=agent["id"],
            task=description.strip() if description else None,
            metadata={"function": fn.__name__, "sdk_version": __version__},
        )
        ctx = _ExecutionContext(record["id"], self._transport)
        self._contexts[ctx.execution_id] = ctx
        token = _current.set(ctx)
        ctx.poller = asyncio.create_task(self._poll_loop(ctx))

        ctx.emit(E.EXECUTION_STARTED, {"task": record.get("task"), "agent": agent["name"]})
        logger.info("reliability: execution %s started", ctx.execution_id)

        try:
            await self._enforce(ctx, label="execution.start")
            result = await fn(*args, **kwargs)
        except ExecutionCancelled:
            # The cancelled event and its acknowledgement were already sent by
            # the checkpoint that raised.
            raise
        except BaseException as exc:
            ctx.emit(
                E.EXECUTION_FAILED,
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[: self.capture.max_preview_chars]
                    if self.capture.capture_error_messages
                    else None,
                },
            )
            raise
        else:
            payload: dict[str, Any] = {}
            if self.capture.capture_output_summary and result is not None:
                payload["output_summary"] = preview(result, self.capture)
            ctx.emit(E.EXECUTION_COMPLETED, payload)
            return result
        finally:
            ctx.finished = True
            if ctx.poller is not None:
                ctx.poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ctx.poller
            await ctx.flush()
            self._contexts.pop(ctx.execution_id, None)
            _current.reset(token)

    @asynccontextmanager
    async def tool(
        self, name: str, args: Any = None, *, call_id: str | None = None
    ) -> AsyncIterator[ToolCall]:
        """Instrument one tool invocation, with a checkpoint before it runs."""
        ctx = self._require_context()
        await self._enforce(ctx, label=f"tool:{name}", tool_name=name)

        call = ToolCall(name, call_id or uuid.uuid4().hex[:12])
        payload: dict[str, Any] = {
            "tool_name": name,
            "tool_call_id": call.call_id,
            # Always sent: the hash is what makes loop detection work without
            # transmitting the arguments themselves.
            "args_fingerprint": fingerprint(args),
            "args_shape": arg_shape(args),
        }
        if self.capture.capture_tool_args and args is not None:
            payload["args"] = redact(args, self.capture)

        ctx.emit(E.TOOL_STARTED, payload)
        started = time.perf_counter()
        try:
            yield call
        except BaseException as exc:
            ctx.emit(
                E.TOOL_FAILED,
                {
                    "tool_name": name,
                    "tool_call_id": call.call_id,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[: self.capture.max_preview_chars]
                    if self.capture.capture_error_messages
                    else None,
                },
            )
            raise
        else:
            completed: dict[str, Any] = {
                "tool_name": name,
                "tool_call_id": call.call_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if self.capture.capture_tool_results and call._has_result:
                completed["result_preview"] = preview(call.result, self.capture)
            ctx.emit(E.TOOL_COMPLETED, completed)

    @asynccontextmanager
    async def llm(self, model: str, prompt: Any = None) -> AsyncIterator[LlmCall]:
        """Instrument one model call, with a checkpoint before it runs."""
        ctx = self._require_context()
        await self._enforce(ctx, label=f"llm:{model}")

        call = LlmCall(model)
        requested: dict[str, Any] = {"model": model}
        if self.capture.capture_prompts and prompt is not None:
            requested["prompt_preview"] = preview(prompt, self.capture)
        ctx.emit(E.LLM_REQUESTED, requested)

        started = time.perf_counter()
        try:
            yield call
        except BaseException as exc:
            ctx.emit(
                E.LLM_FAILED,
                {
                    "model": model,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[: self.capture.max_preview_chars]
                    if self.capture.capture_error_messages
                    else None,
                },
            )
            raise
        else:
            completed: dict[str, Any] = {
                "model": model,
                "prompt_tokens": call.prompt_tokens,
                "completion_tokens": call.completion_tokens,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if self.capture.capture_completions and call.completion is not None:
                completed["completion_preview"] = preview(call.completion, self.capture)
            ctx.emit(E.LLM_COMPLETED, completed)

    def log(self, message: str, **fields: Any) -> None:
        """Attach a free-form note to the current execution trace."""
        ctx = self._require_context()
        ctx.emit(E.LOG, {"message": message[:2000], **redact(fields, self.capture)})
