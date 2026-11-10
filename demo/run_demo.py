"""Run a demonstration agent against a local Reliability backend.

    python demo/run_demo.py loop
    python demo/run_demo.py --list
    python demo/run_demo.py loop --autopilot   # let the supervisor pause it

Reads RELIABILITY_API_KEY and RELIABILITY_API_URL from the project .env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def load_env() -> None:
    """Minimal .env loader - avoids a dependency for a six-line job."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_env()

import httpx  # noqa: E402  (after load_env, so the base URL is available)

from agents import SCENARIOS  # noqa: E402
from reliability import ExecutionCancelled  # noqa: E402

API_URL = os.environ.get("RELIABILITY_API_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("RELIABILITY_API_KEY", "")


async def set_autopilot(agent_name: str) -> None:
    """Authorise the supervisor to pause this agent automatically.

    Scoped deliberately: autopilot may pause, and nothing else. Cancelling
    stays a human decision.
    """
    async with httpx.AsyncClient(
        base_url=API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30
    ) as client:
        agents = (await client.get("/v1/agents")).json()
        agent = next((a for a in agents if a["name"] == agent_name), None)
        if agent is None:
            print(f"  ! agent '{agent_name}' is not registered yet; run it once first.")
            return
        response = await client.put(
            "/v1/policies",
            json={
                "agent_id": agent["id"],
                "name": f"{agent_name}-autopilot",
                "supervision_mode": "autopilot",
                "configuration": {
                    "max_duration_seconds": 300,
                    "max_tool_calls": 50,
                    "max_tokens": 100000,
                    "max_cost_usd": 1.0,
                    "repeated_tool_call_threshold": 3,
                    "repeated_error_threshold": 3,
                    "permitted_interventions": ["pause"],
                    "require_approval_for": [],
                },
            },
        )
        response.raise_for_status()
        print(f"  policy: autopilot enabled for '{agent_name}' (may pause, may not cancel)")


async def report(agent_name: str) -> None:
    async with httpx.AsyncClient(
        base_url=API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30
    ) as client:
        agents = (await client.get("/v1/agents")).json()
        agent = next((a for a in agents if a["name"] == agent_name), None)
        if agent is None:
            return
        executions = (
            await client.get("/v1/executions", params={"agent_id": agent["id"], "limit": 1})
        ).json()
        if not executions:
            return
        execution = executions[0]
        incidents = (
            await client.get("/v1/incidents", params={"execution_id": execution["id"]})
        ).json()

        print()
        print("  Supervisor record")
        print("  " + "-" * 62)
        print(f"  execution    {execution['id']}")
        print(f"  status       {execution['status']}")
        print(
            f"  tool calls   {execution['tool_call_count']}   "
            f"errors {execution['error_count']}   tokens {execution['token_usage']:,}"
        )
        cost = execution.get("estimated_cost")
        if cost is not None:
            suffix = "" if execution.get("cost_complete", True) else "  (partial)"
            print(f"  est. cost    ${cost:.4f}{suffix}")
        if not incidents:
            print("  incidents    none")
        for incident in incidents:
            print(f"  incident     [{incident['severity']}] {incident['title']}")
            evidence = incident.get("evidence", {})
            interesting = {
                k: v
                for k, v in evidence.items()
                if k not in ("events",) and not isinstance(v, (list, dict))
            }
            print(f"               evidence: {interesting}")
            if incident.get("action_taken"):
                print(f"               action:   {incident['action_taken']}")

        interventions = (
            await client.get(f"/v1/executions/{execution['id']}/interventions")
        ).json()
        for iv in interventions:
            confirmed = "runtime-confirmed" if iv["applied_at"] else "not yet confirmed"
            print(
                f"  intervention {iv['action']} by {iv['requested_by']} "
                f"-> {iv['status']} ({confirmed})"
            )
        print()
        print(f"  Dashboard: http://localhost:3000/executions/{execution['id']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Reliability demo agent.")
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="which agent to run")
    parser.add_argument("--list", action="store_true", help="list the scenarios")
    parser.add_argument(
        "--autopilot",
        action="store_true",
        help="let the supervisor pause this agent automatically when it misbehaves",
    )
    args = parser.parse_args()

    if args.list or not args.scenario:
        print("\n  Demo scenarios\n  " + "-" * 62)
        for name, meta in SCENARIOS.items():
            print(f"  {name:<12} {meta['expect']}")
        print()
        return 0

    if not API_KEY:
        print("RELIABILITY_API_KEY is not set. Run: python -m app.seed (in backend/)")
        return 1

    scenario = SCENARIOS[args.scenario]
    print(f"\n  Running '{args.scenario}' against {API_URL}")
    print(f"  Expect: {scenario['expect']}")

    if args.autopilot:
        await set_autopilot(scenario["agent"])

    print()
    try:
        result = await scenario["run"]()
        print(f"  agent returned: {result}")
    except ExecutionCancelled as exc:
        # The runtime actually stopped, and has already told the backend so.
        print(f"  agent CANCELLED by the control plane: {exc}")
    except Exception as exc:
        print(f"  agent FAILED: {type(exc).__name__}: {exc}")

    # Give the final event batch a moment to land before reading it back.
    await asyncio.sleep(1.0)
    await report(scenario["agent"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
