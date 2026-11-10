"""Demonstration agents.

Five deliberately-shaped agents, each exercising one failure mode the
supervisor is meant to catch. They are fully deterministic and need no model
provider or API key of their own - the token counts they report are the
counts they actually "used", so every figure in the dashboard is arithmetic
over real reported values rather than an invented number.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from reliability import CaptureConfig, Supervisor

DEMO_MODEL = "demo-model"

# A tiny fixed corpus, so "search" results are reproducible run to run.
CORPUS = {
    "ai startups": [
        "Anthropic - frontier model research",
        "Cursor - AI-native code editor",
        "Harvey - legal workflows",
    ],
    "agent observability": [
        "Tracing is table stakes; control is not",
        "Most teams discover loops from their billing page",
    ],
}


def make_supervisor(name: str, description: str, **kwargs: Any) -> Supervisor:
    return Supervisor(
        agent_id=name,
        framework="custom",
        description=description,
        # The demo opts in to result and output capture so the trace viewer
        # has something to show. The SDK default keeps all of this local.
        capture=CaptureConfig(
            capture_tool_args=True,
            capture_tool_results=True,
            capture_output_summary=True,
        ),
        **kwargs,
    )


async def _search(query: str) -> list[str]:
    await asyncio.sleep(0.15)
    return CORPUS.get(query.lower(), [f"No results for {query!r}"])


# ---------------------------------------------------------------------------
# 1. Healthy research agent
# ---------------------------------------------------------------------------

async def run_healthy() -> str:
    supervisor = make_supervisor(
        "healthy-research-agent", "Completes a short research task cleanly."
    )

    @supervisor.monitor(task="Research AI startups and summarise the field")
    async def research() -> str:
        findings: list[str] = []
        for query in ("AI startups", "agent observability"):
            async with supervisor.tool("search", {"q": query}) as call:
                results = call.set_result(await _search(query))
            findings.extend(results)

        async with supervisor.llm(DEMO_MODEL, prompt="Summarise findings") as call:
            await asyncio.sleep(0.3)
            summary = (
                f"Reviewed {len(findings)} sources. The common thread: teams can see "
                "what their agents did, but cannot intervene while it is happening."
            )
            call.record_usage(prompt_tokens=820, completion_tokens=240)
            call.set_completion(summary)
        return summary

    try:
        return await research()
    finally:
        await supervisor.aclose()


# ---------------------------------------------------------------------------
# 2. Infinite loop agent
# ---------------------------------------------------------------------------

async def run_loop(iterations: int = 40, delay: float = 0.4) -> str:
    """Calls one tool with identical arguments, forever making no progress.

    Bounded by ``iterations`` so an unattended demo terminates, but long
    enough that a human can pause or cancel it from the dashboard mid-flight.
    """
    supervisor = make_supervisor(
        "infinite-loop-agent", "Repeats an identical tool call without progressing."
    )

    @supervisor.monitor(task="Find the definitive list of AI startups")
    async def loop_forever() -> str:
        for i in range(iterations):
            # Identical arguments every time: the fingerprint never changes,
            # which is exactly what the repetition detector keys on.
            async with supervisor.tool("search", {"q": "AI startups"}) as call:
                call.set_result(await _search("AI startups"))
            supervisor.log(f"iteration {i + 1}: still not satisfied with the results")
            await asyncio.sleep(delay)
        return f"Gave up after {iterations} identical searches."

    try:
        return await loop_forever()
    finally:
        await supervisor.aclose()


# ---------------------------------------------------------------------------
# 3. Failing agent
# ---------------------------------------------------------------------------

class UpstreamUnavailable(RuntimeError):
    pass


async def run_failing(attempts: int = 6) -> str:
    supervisor = make_supervisor(
        "failing-agent", "Retries a tool whose dependency is down."
    )

    @supervisor.monitor(task="Fetch the quarterly numbers from the finance API")
    async def fetch_with_retries() -> str:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with supervisor.tool(
                    "finance_api.fetch", {"quarter": "Q3", "attempt": attempt}
                ):
                    await asyncio.sleep(0.2)
                    raise UpstreamUnavailable("finance-api returned 503 Service Unavailable")
            except UpstreamUnavailable as exc:
                # The agent "handles" the error and retries - which is the
                # behaviour that makes repeated failure invisible without
                # supervision.
                last_error = exc
                supervisor.log(f"attempt {attempt + 1} failed, retrying")
                await asyncio.sleep(0.3)
        raise RuntimeError(f"All {attempts} attempts failed: {last_error}")

    try:
        return await fetch_with_retries()
    finally:
        await supervisor.aclose()


# ---------------------------------------------------------------------------
# 4. Expensive agent
# ---------------------------------------------------------------------------

async def run_expensive(calls: int = 6, tokens_per_call: int = 30_000) -> str:
    """Burns through the token budget with oversized context on every call."""
    supervisor = make_supervisor(
        "expensive-agent", "Sends an ever-growing context to the model."
    )

    @supervisor.monitor(task="Summarise the entire document corpus, repeatedly")
    async def burn() -> str:
        total = 0
        for i in range(calls):
            async with supervisor.llm(DEMO_MODEL, prompt=f"chunk {i}") as call:
                await asyncio.sleep(0.25)
                prompt_tokens = tokens_per_call
                completion_tokens = tokens_per_call // 10
                call.record_usage(prompt_tokens, completion_tokens)
                call.set_completion(f"Partial summary {i + 1}")
                total += prompt_tokens + completion_tokens
        return f"Produced {calls} partial summaries using {total:,} tokens."

    try:
        return await burn()
    finally:
        await supervisor.aclose()


# ---------------------------------------------------------------------------
# 5. Poor output agent
# ---------------------------------------------------------------------------

async def run_poor_output() -> str:
    """Finishes successfully while answering a different question.

    No deterministic rule catches this: the run is fast, cheap and
    error-free. It is the case that needs the LLM output evaluator, and the
    demo includes it to show where the deterministic layer stops.
    """
    supervisor = make_supervisor(
        "poor-output-agent", "Returns a confident answer to the wrong question."
    )

    @supervisor.monitor(
        task="Produce a competitive analysis of the top 3 AI observability vendors"
    )
    async def half_answer() -> str:
        async with supervisor.tool("search", {"q": "agent observability"}) as call:
            call.set_result(await _search("agent observability"))

        async with supervisor.llm(DEMO_MODEL, prompt="Write the analysis") as call:
            await asyncio.sleep(0.2)
            output = "Observability is important for AI agents."
            call.record_usage(prompt_tokens=400, completion_tokens=12)
            call.set_completion(output)
        return output

    try:
        return await half_answer()
    finally:
        await supervisor.aclose()


# ---------------------------------------------------------------------------
# 6. Long-running agent, for exercising pause/resume/cancel by hand
# ---------------------------------------------------------------------------

async def run_long(steps: int = 60, delay: float = 1.0) -> str:
    """Does varied, genuinely-progressing work slowly.

    Nothing here is wrong, which is the point: use it to try pause, resume
    and cancel from the dashboard without an incident muddying the picture.
    """
    supervisor = make_supervisor(
        "long-running-agent", "A healthy agent that runs long enough to interrupt."
    )

    @supervisor.monitor(task="Process a backlog of documents")
    async def process() -> str:
        processed = 0
        for step in range(steps):
            async with supervisor.tool("process_document", {"doc_id": step}) as call:
                await asyncio.sleep(delay)
                call.set_result({"doc_id": step, "words": random.randint(200, 900)})
            processed += 1
            # Explicit checkpoint: control is honoured between tool calls too.
            await supervisor.checkpoint(f"document {step}")
        return f"Processed {processed} documents."

    try:
        return await process()
    finally:
        await supervisor.aclose()


SCENARIOS: dict[str, dict[str, Any]] = {
    "healthy": {
        "run": run_healthy,
        "agent": "healthy-research-agent",
        "expect": "Completes cleanly. No incident.",
    },
    "loop": {
        "run": run_loop,
        "agent": "infinite-loop-agent",
        "expect": "repeated_tool_call incident once the same call repeats past the threshold.",
    },
    "failing": {
        "run": run_failing,
        "agent": "failing-agent",
        "expect": "repeated_errors incident, then the run fails.",
    },
    "expensive": {
        "run": run_expensive,
        "agent": "expensive-agent",
        "expect": "token_budget incident once reported usage passes the limit.",
    },
    "poor-output": {
        "run": run_poor_output,
        "agent": "poor-output-agent",
        "expect": "No deterministic incident - needs the LLM output evaluator.",
    },
    "long": {
        "run": run_long,
        "agent": "long-running-agent",
        "expect": "Healthy but slow. Use it to try pause / resume / cancel.",
    },
}
