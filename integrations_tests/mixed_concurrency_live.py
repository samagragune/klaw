"""Live test: Claude Code and OpenAI Agents SDK agents running at the same time.

Starts two real headless Claude Code sessions and three real Agents SDK runs
(the helpdesk app) concurrently against one supervisor, then checks that:

* every run became its own session, under the right platform and agent,
* no event leaked between sessions (each session's tool calls are the ones
  its agent made),
* the overview's per-platform counts reflect both platforms.

    python integrations_tests/mixed_concurrency_live.py --project <claude-code project with hooks>
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openai_agents_live import API, api, configure_provider, helpdesk_profile, record  # noqa: E402

CC_PROMPTS = [
    "Print the current directory name using one shell command and report it.",
    "Count the files in the current directory with one shell command and report the number.",
]
OA_PROMPTS = [
    "What's the status of the wiki?",
    "Look up invoice INV-3001 please.",
    "Is email working right now?",
]


async def run_claude(project: Path, prompt: str) -> dict:
    exe = shutil.which("claude")
    proc = await asyncio.create_subprocess_exec(
        exe, "-p", prompt, "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash PowerShell Read Glob", "--max-turns", "6", "--output-format", "json",
        cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=240)
    except asyncio.TimeoutError:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return {"session_id": None, "result": "timeout"}
    import json

    try:
        return json.loads(out.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {"session_id": None, "result": out.decode("utf-8", "replace")[-300:]}


def cc_record(external_id: str) -> dict:
    for s in api("GET", "/v1/executions?limit=100&platform=claude_code"):
        if s["external_session_id"] == external_id:
            return api("GET", f"/v1/executions/{s['id']}")
    return {}


async def main_async(project: Path) -> int:
    configure_provider()
    app = helpdesk_profile()
    key = api("POST", "/v1/integrations", {"platform": "openai_agents", "name": "mixed-test"})["key"]
    from reliability.integrations.openai_agents import AgentsSupervisor

    sup = AgentsSupervisor(app_name="helpdesk-mixed", api_url=API, integration_key=key)
    api("PUT", "/v1/policies", {"supervision_mode": "control", "name": "mixed", "configuration": {}})

    stamp = int(time.time())
    oa_sessions = [f"mixed-{stamp}-{i}" for i in range(len(OA_PROMPTS))]
    started = time.time()
    results = await asyncio.gather(
        *[run_claude(project, p) for p in CC_PROMPTS],
        *[sup.run(app.triage, p, context=app.context(), session_id=s) for p, s in zip(OA_PROMPTS, oa_sessions)],
        return_exceptions=True,
    )
    elapsed = time.time() - started
    await asyncio.sleep(2)

    cc_results = results[: len(CC_PROMPTS)]
    oa_results = results[len(CC_PROMPTS):]
    cc_recs = [cc_record(r.get("session_id")) if isinstance(r, dict) and r.get("session_id") else {} for r in cc_results]
    oa_recs = [record(s) for s in oa_sessions]

    ok = True
    print(f"\n5 agents ran concurrently in {elapsed:.0f}s\n")
    for label, rec in [*(("claude_code", r) for r in cc_recs), *(("openai_agents", r) for r in oa_recs)]:
        if not rec:
            print(f"  [FAIL] {label}: no session recorded")
            ok = False
            continue
        platforms = {rec["platform"]}
        tools = sorted({e["payload"].get("tool_name") for e in rec["events"] if e["event_type"] == "TOOL_CALL_STARTED"})
        good = rec["platform"] == label and rec["status"] in ("completed", "idle") and rec["event_count"] > 0
        # Claude Code tools are capitalised built-ins; helpdesk tools are snake_case.
        leaked = [t for t in tools if t and ((label == "claude_code") == ("_" in t))]
        good = good and not leaked
        ok &= good
        print(f"  [{'ok' if good else 'FAIL'}] {label:<14} {rec['agent_name'][:40]:<40} status={rec['status']:<9} "
              f"events={rec['event_count']:<3} tools={tools}{' LEAKED ' + str(leaked) if leaked else ''}")

    ids = [r.get("id") for r in cc_recs + oa_recs if r]
    distinct = len(set(ids)) == len(ids) == len(CC_PROMPTS) + len(OA_PROMPTS)
    ok &= distinct
    print(f"\n  [{'ok' if distinct else 'FAIL'}] {len(set(ids))} distinct sessions for {len(ids)} runs")
    platforms = {p["platform"]: p for p in api("GET", "/v1/metrics/platforms")}
    both = "claude_code" in platforms and "openai_agents" in platforms
    ok &= both
    print(f"  [{'ok' if both else 'FAIL'}] overview shows both platforms: "
          + ", ".join(f"{k}: {v['agents']} agents / {v['sessions']} sessions" for k, v in platforms.items()))
    for r in oa_results:
        if isinstance(r, Exception):
            print(f"  agents run error: {r!r}")
    sup.close()
    print(f"\n=== summary: mixed={'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    return asyncio.run(main_async(Path(args.project)))


if __name__ == "__main__":
    sys.exit(main())
