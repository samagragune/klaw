"""End-to-end proof that Reliability can actually control a running agent.

Runs the looping agent in this process, then drives the control plane over
HTTP exactly as the dashboard would, checking at each step that the *runtime*
confirmed the action rather than trusting the command was enough:

    1. wait for the deterministic loop incident to be raised
    2. pause        -> intervention applied, execution paused, tool calls stop
    3. resume       -> intervention applied, tool calls start again
    4. cancel       -> intervention applied, agent raises ExecutionCancelled

Every assertion is against state read back from the backend.

    python demo/control_demo.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_demo import API_KEY, API_URL, load_env  # noqa: E402

load_env()

import httpx  # noqa: E402

from agents import run_loop  # noqa: E402
from reliability import ExecutionCancelled  # noqa: E402

AGENT_NAME = "infinite-loop-agent"
TIMEOUT = 45.0

PASS = "  [ok]  "
FAIL = "  [FAIL]"


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def that(self, condition: bool, description: str, detail: str = "") -> bool:
        if condition:
            print(f"{PASS} {description}" + (f"  ({detail})" if detail else ""))
        else:
            print(f"{FAIL} {description}" + (f"  ({detail})" if detail else ""))
            self.failures.append(description)
        return condition


async def wait_for(predicate, timeout: float = TIMEOUT, interval: float = 0.4):
    """Poll ``predicate`` until it returns something truthy, or time out."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    return None


async def main() -> int:
    if not API_KEY:
        print("RELIABILITY_API_KEY is not set. Run `python -m app.seed` in backend/.")
        return 1

    checks = Checks()
    client = httpx.AsyncClient(
        base_url=API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30
    )

    async def execution_detail(execution_id: str) -> dict:
        return (await client.get(f"/v1/executions/{execution_id}")).json()

    async def intervention(intervention_id: str, execution_id: str) -> dict:
        rows = (await client.get(f"/v1/executions/{execution_id}/interventions")).json()
        return next(r for r in rows if r["id"] == intervention_id)

    print("\n  Reliability - execution control acceptance run")
    print("  " + "=" * 62)

    # Run the agent long enough to be interrupted several times.
    agent_task = asyncio.create_task(run_loop(iterations=200, delay=0.4))
    cancelled_cleanly = False

    try:
        # -- find the execution the SDK opened ------------------------------
        async def find_execution():
            rows = (await client.get("/v1/executions", params={"active": True})).json()
            agents = (await client.get("/v1/agents")).json()
            agent = next((a for a in agents if a["name"] == AGENT_NAME), None)
            if agent is None:
                return None
            mine = [r for r in rows if r["agent_id"] == agent["id"]]
            return mine[0] if mine else None

        execution = await wait_for(find_execution, timeout=20)
        if not checks.that(execution is not None, "SDK opened an execution"):
            return 1
        execution_id = execution["id"]
        print(f"         execution {execution_id}")

        # -- 1. deterministic detection -------------------------------------
        async def loop_incident():
            rows = (
                await client.get("/v1/incidents", params={"execution_id": execution_id})
            ).json()
            return next((r for r in rows if r["category"] == "repeated_tool_call"), None)

        incident = await wait_for(loop_incident)
        checks.that(incident is not None, "loop detected without any LLM involved")
        if incident:
            evidence = incident["evidence"]
            checks.that(
                evidence.get("occurrences", 0) >= evidence.get("threshold", 3),
                "incident evidence cites real repeated events",
                f"{evidence.get('occurrences')} calls to "
                f"'{evidence.get('tool_name')}', threshold {evidence.get('threshold')}",
            )
            checks.that(
                bool(evidence.get("events")),
                "evidence links back to stored trace events",
                f"{len(evidence.get('events', []))} event references",
            )

        # -- 2. pause --------------------------------------------------------
        before = await execution_detail(execution_id)
        response = await client.post(
            f"/v1/executions/{execution_id}/interventions",
            json={"action": "pause", "reason": "acceptance run: pause"},
        )
        response.raise_for_status()
        pause = response.json()
        checks.that(
            pause["status"] == "pending" and pause["applied_at"] is None,
            "pause starts as pending, not reported as applied",
        )

        applied = await wait_for(
            lambda: _applied(intervention(pause["id"], execution_id))
        )
        checks.that(applied is not None, "runtime confirmed the pause was applied")

        paused = await wait_for(
            lambda: _status(execution_detail(execution_id), "paused")
        )
        checks.that(paused is not None, "execution reports status=paused")

        # The real test: has the agent actually stopped doing work?
        calls_at_pause = (await execution_detail(execution_id))["tool_call_count"]
        await asyncio.sleep(3.0)
        calls_after = (await execution_detail(execution_id))["tool_call_count"]
        checks.that(
            calls_after == calls_at_pause,
            "agent stopped making tool calls while paused",
            f"{calls_at_pause} -> {calls_after} over 3s",
        )
        checks.that(not agent_task.done(), "agent coroutine is suspended, not finished")

        # -- 3. resume -------------------------------------------------------
        resume = (
            await client.post(
                f"/v1/executions/{execution_id}/interventions",
                json={"action": "resume", "reason": "acceptance run: resume"},
            )
        ).json()
        resumed = await wait_for(
            lambda: _applied(intervention(resume["id"], execution_id))
        )
        checks.that(resumed is not None, "runtime confirmed the resume was applied")

        progressed = await wait_for(
            lambda: _grew(execution_detail(execution_id), calls_after), timeout=20
        )
        checks.that(
            progressed is not None,
            "agent resumed making tool calls",
            f"{calls_after} -> {progressed}" if progressed else "",
        )

        # -- 4. cancel -------------------------------------------------------
        cancel = (
            await client.post(
                f"/v1/executions/{execution_id}/interventions",
                json={"action": "cancel", "reason": "acceptance run: cancel"},
            )
        ).json()

        try:
            await asyncio.wait_for(agent_task, timeout=30)
        except ExecutionCancelled:
            cancelled_cleanly = True
        except asyncio.TimeoutError:
            pass
        checks.that(
            cancelled_cleanly,
            "agent code raised ExecutionCancelled at a checkpoint",
        )

        cancel_applied = await wait_for(
            lambda: _applied(intervention(cancel["id"], execution_id))
        )
        checks.that(cancel_applied is not None, "runtime confirmed the cancel was applied")

        final = await wait_for(
            lambda: _status(execution_detail(execution_id), "cancelled")
        )
        checks.that(final is not None, "execution reports status=cancelled")

    finally:
        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, ExecutionCancelled):
                pass
        await client.aclose()

    print("  " + "=" * 62)
    if checks.failures:
        print(f"  {len(checks.failures)} check(s) FAILED:")
        for failure in checks.failures:
            print(f"    - {failure}")
        return 1
    print("  all control checks passed")
    return 0


# Small helpers that await a coroutine and test one property of the result.

async def _applied(awaitable) -> dict | None:
    row = await awaitable
    return row if row.get("applied_at") else None


async def _status(awaitable, expected: str) -> dict | None:
    row = await awaitable
    return row if row.get("status") == expected else None


async def _grew(awaitable, baseline: int) -> int | None:
    row = await awaitable
    count = row.get("tool_call_count", 0)
    return count if count > baseline else None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
