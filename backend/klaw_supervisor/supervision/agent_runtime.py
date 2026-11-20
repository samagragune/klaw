"""The Klaw agent's reasoning loop: think, use a tool, look at the result, repeat.

``run_agent`` gives the model a goal, a set of tools and a budget (steps and
seconds). The model works until it calls the ``finish`` function with its
conclusion, or the budget runs out (then it is made to conclude with what it
has). Every step is recorded so the operator can see how it reached a
decision: which files it read, what the diff showed, what the tests said.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from klaw_supervisor.models import EvaluatorCall
from klaw_supervisor.pricing import estimate_cost
from klaw_supervisor.supervision.agent_tools import Tool, ToolContext
from klaw_supervisor.supervision.providers import EvaluatorUnavailable, get_provider

logger = logging.getLogger(__name__)

TOOL_TIMEOUT = 120.0


@dataclass
class AgentRun:
    finished: bool
    result: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    seconds: float = 0.0
    error: str | None = None

    def summary(self) -> list[dict[str, Any]]:
        return [
            {"tool": s["tool"], "args": s["args"], "result": str(s["result"])[:400]}
            for s in self.steps
        ]


def tools_available(tier: str = "fast") -> bool:
    try:
        return bool(getattr(get_provider(tier), "supports_tools", False))
    except EvaluatorUnavailable:
        return False


async def run_agent(
    ctx: ToolContext,
    *,
    purpose: str,
    system: str,
    goal: str,
    tools: list[Tool],
    finish: dict[str, Any],
    max_steps: int = 8,
    seconds: float = 60.0,
    tier: str = "fast",
) -> AgentRun:
    """Work toward ``goal`` with ``tools`` until the model calls ``finish``.

    ``finish`` is the JSON schema of the conclusion; the model must call a
    function named ``finish`` with it.
    """
    started = time.monotonic()
    deadline = started + seconds
    provider = get_provider(tier)
    by_name = {tool.name: tool for tool in tools}
    declarations = [tool.declaration() for tool in tools] + [
        {"name": "finish", "description": "Give your conclusion. Ends the task.", "parameters": finish}
    ]
    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": goal}]}]
    run = AgentRun(finished=False, result={})

    async def turn(force: list[str] | None = None) -> dict[str, Any]:
        remaining = max(3.0, deadline - time.monotonic())
        t0 = time.perf_counter()
        ok = False
        try:
            content = await asyncio.wait_for(
                provider.converse(system, contents, declarations, force=force), timeout=remaining
            )
            ok = True
            return content
        finally:
            run.model_calls += 1
            prompt_tokens, completion_tokens = getattr(provider, "last_usage", (0, 0))
            ctx.session.add(EvaluatorCall(
                user_id=ctx.execution.user_id, purpose=purpose, model=provider.model,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                estimated_cost=estimate_cost(provider.model, prompt_tokens, completion_tokens),
                latency_ms=round((time.perf_counter() - t0) * 1000, 1), ok=ok,
            ))

    try:
        for step in range(max_steps):
            if time.monotonic() > deadline - 2:
                break
            content = await turn()
            contents.append(content)
            calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
            if not calls:
                # The model answered in text; ask it to conclude properly.
                contents.append({"role": "user", "parts": [{"text": "Call finish with your conclusion."}]})
                continue
            replies = []
            for call in calls:
                name = call.get("name")
                args = call.get("args") or {}
                if name == "finish":
                    run.finished = True
                    run.result = args
                    return run
                tool = by_name.get(name)
                if tool is None:
                    output = f"Unknown tool {name}."
                else:
                    try:
                        output = await asyncio.wait_for(tool.run(ctx, args), timeout=TOOL_TIMEOUT)
                    except PermissionError as exc:
                        output = f"Refused: {exc}"
                    except Exception as exc:  # a broken tool must not end the investigation
                        logger.exception("agent tool %s failed", name)
                        output = f"Tool error: {type(exc).__name__}: {exc}"
                run.steps.append({"tool": name, "args": args, "result": output})
                replies.append({"functionResponse": {
                    "name": name,
                    "response": {"untrusted_output": output},
                }})
            contents.append({"role": "user", "parts": replies})
        # Out of steps or time: conclude with what is known.
        contents.append({"role": "user", "parts": [{"text":
            "Your budget is used up. Call finish now with your best conclusion from the evidence so far."}]})
        content = await turn(force=["finish"])
        for part in content.get("parts", []):
            call = part.get("functionCall")
            if call and call.get("name") == "finish":
                run.finished = True
                run.result = call.get("args") or {}
        return run
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.warning("Klaw agent run (%s) failed: %s", purpose, run.error)
        return run
    finally:
        run.seconds = round(time.monotonic() - started, 2)


def render_steps(run: AgentRun) -> str:
    """The investigation, briefly, for the record."""
    lines = []
    for s in run.steps:
        args = json.dumps(s["args"], ensure_ascii=False)[:120]
        first = str(s["result"]).strip().splitlines()[:1]
        lines.append(f"{s['tool']}({args}) -> {first[0][:140] if first else ''}")
    return "\n".join(lines)
