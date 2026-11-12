"""Run real Gemini-powered agents under supervision and check what happened.

    python demo/run_real.py --list
    python demo/run_real.py research
    python demo/run_real.py polling --autopilot
    python demo/run_real.py all            # every scenario, with assertions

Each run sets an agent policy that turns on output evaluation, runs the agent
against the live backend, then reads the record back over the API and checks
it against the scenario's expectation. Because the model really decides what
to do, a run can legitimately differ from the expectation; that is reported
as a FAIL with what actually happened, never papered over.

Needs: the backend running, RELIABILITY_API_KEY and GEMINI_API_KEY in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_demo import load_env  # noqa: E402  (shared minimal .env loader)

load_env()

import httpx  # noqa: E402

from real_agents import SCENARIOS, Scenario, build  # noqa: E402
from reliability import ExecutionCancelled  # noqa: E402

API_URL = os.environ.get("RELIABILITY_API_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("RELIABILITY_API_KEY", "")
MODEL = os.environ.get("DEMO_AGENT_MODEL", "gemini-3.5-flash-lite")

REVIEW_WAIT_SECONDS = 120


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30
    )


async def set_policy(agent_id: str, name: str, autopilot: bool) -> None:
    async with client() as http:
        response = await http.put(
            "/v1/policies",
            json={
                "agent_id": agent_id,
                "name": f"{name}-demo",
                "supervision_mode": "autopilot" if autopilot else "assist",
                "configuration": {
                    "max_duration_seconds": 300,
                    "max_tool_calls": 30,
                    "max_tokens": 200_000,
                    "max_cost_usd": 1.0,
                    "repeated_tool_call_threshold": 3,
                    "repeated_error_threshold": 3,
                    "permitted_interventions": ["pause"],
                    "require_approval_for": [],
                    "evaluate_output_on_completion": True,
                },
            },
        )
        response.raise_for_status()


async def latest_execution(agent_id: str) -> dict[str, Any] | None:
    async with client() as http:
        rows = (
            await http.get("/v1/executions", params={"agent_id": agent_id, "limit": 1})
        ).json()
    return rows[0] if rows else None


async def cancel_when_paused(agent_id: str, previous: str | None, log: list[str]) -> None:
    """Autopilot pauses the agent; a human (this script) then cancels it.

    Proves control works on a live model-driven agent, not just a scripted one.
    """
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        execution = await latest_execution(agent_id)
        if not execution or execution["id"] == previous:
            continue
        if execution["status"] == "paused":
            log.append("autopilot pause applied by the runtime; operator now cancels")
            async with client() as http:
                response = await http.post(
                    f"/v1/executions/{execution['id']}/interventions",
                    json={"action": "cancel", "reason": "Demo: stuck polling, stopping it"},
                )
                response.raise_for_status()
            return
        if execution["status"] in ("completed", "failed", "cancelled"):
            return


async def wait_for_review(execution_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + REVIEW_WAIT_SECONDS
    async with client() as http:
        while True:
            detail = (await http.get(f"/v1/executions/{execution_id}")).json()
            if detail.get("output_evaluation") or time.monotonic() > deadline:
                return detail
            await asyncio.sleep(2.0)


async def run_one(key: str, scenario: Scenario, autopilot: bool) -> bool:
    print(f"\n=== {key}: {scenario.description}")
    print(f"    agent model : {MODEL}")
    print(f"    task        : {scenario.task}")
    print(f"    expect      : {scenario.expect}")

    supervisor, run = build(scenario, MODEL)
    agent = await supervisor.register()
    await set_policy(agent["id"], scenario.agent, autopilot)
    previous = await latest_execution(agent["id"])
    previous_id = previous["id"] if previous else None

    notes: list[str] = []
    watcher = (
        asyncio.create_task(cancel_when_paused(agent["id"], previous_id, notes))
        if autopilot
        else None
    )
    started = time.monotonic()
    outcome: str
    try:
        result = await run()
        outcome = "completed"
        print(f"    answer      : {result[:600]}")
    except ExecutionCancelled as exc:
        outcome = "cancelled"
        print(f"    CANCELLED at a checkpoint: {exc}")
    except Exception as exc:
        outcome = "failed"
        print(f"    FAILED: {type(exc).__name__}: {exc}")
    finally:
        await supervisor.aclose()
        if watcher is not None:
            watcher.cancel()
    for note in notes:
        print(f"    note        : {note}")
    print(f"    runtime     : {time.monotonic() - started:.1f}s")

    await asyncio.sleep(1.0)
    execution = await latest_execution(agent["id"])
    if execution is None or execution["id"] == previous_id:
        print("    [FAIL] no execution was recorded")
        return False

    if outcome == "completed":
        detail = await wait_for_review(execution["id"])
    else:
        async with client() as http:
            detail = (await http.get(f"/v1/executions/{execution['id']}")).json()
    incidents = detail["incidents"]
    categories = {i["category"] for i in incidents}

    print(
        f"    record      : status={detail['status']} tools={detail['tool_call_count']} "
        f"errors={detail['error_count']} tokens={detail['token_usage']:,} "
        f"cost={'n/a (unpriced model)' if not detail['cost_complete'] else detail['estimated_cost']}"
    )
    for incident in incidents:
        print(f"    incident    : [{incident['source']}] {incident['category']} - {incident['title']}")
        if incident.get("action_taken"):
            print(f"                  action: {incident['action_taken']}")
    for iv in detail["interventions"]:
        print(f"    intervention: {iv['action']} by {iv['requested_by']} -> {iv['status']}")
    review = detail.get("output_evaluation")
    if review:
        print(
            f"    review      : satisfies={review.get('satisfies_objective')} "
            f"confidence={review.get('confidence')} ({review.get('evaluator')})"
        )
        if review.get("gaps"):
            for gap in review["gaps"][:4]:
                print(f"                  gap: {gap}")
    elif outcome == "completed":
        print("    review      : none arrived (is SUPERVISOR_LLM_PROVIDER set?)")

    expected = set(scenario.expected_incidents)
    ok = expected <= categories
    if not expected:
        # "Healthy" means no incident at all - including from the evaluator.
        ok = not categories and detail["status"] == "completed"
    if autopilot and "repeated_tool_call" in expected:
        applied = any(
            iv["action"] == "pause" and iv["requested_by"] == "supervisor" and iv["status"] == "applied"
            for iv in detail["interventions"]
        )
        ok = ok and applied
    verdict = "ok" if ok else "FAIL"
    print(
        f"    [{verdict}] expected incidents {sorted(expected) or 'none'}, "
        f"got {sorted(categories) or 'none'}"
    )
    print(f"    dashboard   : http://localhost:3000/executions/{execution['id']}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run real Gemini agents under Reliability.")
    parser.add_argument("scenario", nargs="?", help="scenario name, or 'all'")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--autopilot", action="store_true", help="let the supervisor pause misbehaving agents"
    )
    args = parser.parse_args()

    if args.list or not args.scenario:
        print("\n  Real-agent scenarios (Gemini function calling)\n  " + "-" * 62)
        for name, s in SCENARIOS.items():
            print(f"  {name:<10} {s.expect}")
        print()
        return 0
    if not API_KEY:
        print("RELIABILITY_API_KEY is not set. Run: python -m app.seed (in backend/)")
        return 1
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set in .env")
        return 1

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenario {unknown[0]!r}. Try --list.")
        return 1

    results = {}
    for name in names:
        # The polling trap is the one where autopilot matters; in 'all' mode
        # run it with autopilot so control is exercised on a live model.
        autopilot = args.autopilot or (args.scenario == "all" and name == "polling")
        results[name] = await run_one(name, SCENARIOS[name], autopilot)

    print("\n=== summary")
    for name, ok in results.items():
        print(f"    [{'ok' if ok else 'FAIL'}] {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
