"""Live integration test: OpenAI Agents SDK applications under supervision.

Two applications can be driven, with the same scenarios:

* ``--app helpdesk`` - ``integrations_tests/agents_app/helpdesk.py``, a
  multi-agent app written for this repo.
* ``--app examples --examples <dir>`` - OpenAI's own example apps imported
  unmodified from a checkout of openai-agents-python (``customer_service``,
  ``agent_patterns/input_guardrails``). Running these executes third-party
  code, so it is opt-in.

In both cases the only change is the model provider: the SDK's default client
is pointed at Gemini's OpenAI-compatible endpoint, using the SDK's own
configuration functions.

While a run executes, this script plays the operator through the
supervisor's REST API (approve, deny, pause, resume, stop), then reads back
the record and checks it.

    python integrations_tests/openai_agents_live.py --app helpdesk [scenario ...]
    python integrations_tests/openai_agents_live.py --app examples --examples <oa-src>/examples
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(
    line.split("=", 1)
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    if "=" in line and not line.startswith("#")
)
API = "http://127.0.0.1:8000"
USER_KEY = ENV["RELIABILITY_API_KEY"].strip()
MODEL = ENV.get("DEMO_AGENT_MODEL", "gemini-3.5-flash-lite").strip()


def api(method: str, path: str, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {USER_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def configure_provider() -> None:
    from openai import AsyncOpenAI

    from agents import set_default_openai_api, set_default_openai_client, set_trace_processors

    os.environ["OPENAI_DEFAULT_MODEL"] = MODEL
    set_default_openai_client(
        AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=ENV["GEMINI_API_KEY"].strip(),
        ),
        use_for_tracing=False,
    )
    set_default_openai_api("chat_completions")
    # No OpenAI key: drop the default exporter (it would upload traces to
    # OpenAI). The Reliability processor is added by the supervisor.
    set_trace_processors([])


def set_policy(**config) -> None:
    api("PUT", "/v1/policies", {"supervision_mode": "control", "name": "live-test", "configuration": config})


def record(session_id: str) -> dict:
    for s in api("GET", "/v1/executions?limit=100&platform=openai_agents"):
        if s["external_session_id"] == session_id:
            return api("GET", f"/v1/executions/{s['id']}")
    return {}


def summarise(rec: dict) -> list[str]:
    lines = [
        f"session status={rec.get('status')} tools={rec.get('tool_call_count')} "
        f"tokens={rec.get('token_usage')} events={rec.get('event_count')}"
    ]
    counts: dict[str, int] = {}
    for e in rec.get("events", []):
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    lines.append("events: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for e in rec.get("events", []):
        if e["event_type"] == "AGENT_HANDOFF":
            lines.append(f"handoff {e['payload'].get('from_agent')} -> {e['payload'].get('to_agent')}")
    for d in rec.get("decisions", []):
        lines.append(f"decision {d['action']} ({d['rule']}) tool={d['tool_name']}")
    for a in rec.get("approvals", []):
        lines.append(f"approval {a['status']} for {a['tool_name']}")
    for i in rec.get("interventions", []):
        lines.append(f"intervention {i['action']} -> {i['status']}")
    for i in rec.get("incidents", []):
        lines.append(f"incident [{i['source']}] {i['category']}")
    return lines


class Operator:
    """Acts on the dashboard API from a background task."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self._task: asyncio.Task | None = None

    def start(self, coro) -> None:
        self._task = asyncio.create_task(coro)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def decide_approvals(self, approve: bool) -> None:
        seen: set[str] = set()
        while True:
            for a in await asyncio.to_thread(api, "GET", "/v1/approvals?status=pending"):
                if a["id"] not in seen:
                    seen.add(a["id"])
                    await asyncio.sleep(1)
                    await asyncio.to_thread(
                        api, "POST", f"/v1/approvals/{a['id']}/decision",
                        {"approve": approve, "note": "live test operator"},
                    )
                    self.log.append(f"{'approved' if approve else 'denied'} {a['tool_name']} {a['action_preview']}")
            await asyncio.sleep(0.5)

    async def pause_then_resume(self, session_id: str, hold: float = 4.0) -> None:
        while True:
            rec = await asyncio.to_thread(record, session_id)
            if rec and rec.get("event_count", 0) >= 3:
                break
            await asyncio.sleep(0.3)
        await asyncio.to_thread(api, "POST", f"/v1/executions/{rec['id']}/interventions",
                                {"action": "pause", "reason": "live test pause"})
        self.log.append("pause issued")
        while (await asyncio.to_thread(record, session_id)).get("status") != "paused":
            await asyncio.sleep(0.3)
        frozen = (await asyncio.to_thread(record, session_id))["event_count"]
        await asyncio.sleep(hold)
        after = (await asyncio.to_thread(record, session_id))["event_count"]
        # +0 or +1: the pause acknowledgement itself may land just after.
        self.log.append(f"while paused: events {frozen} -> {after} over {hold}s")
        self.frozen_ok = after - frozen <= 1
        await asyncio.to_thread(api, "POST", f"/v1/executions/{rec['id']}/interventions",
                                {"action": "resume", "reason": "live test resume"})
        self.log.append("resume issued")

    async def stop_after(self, session_id: str, events: int) -> None:
        while True:
            rec = await asyncio.to_thread(record, session_id)
            if rec and rec.get("event_count", 0) >= events:
                break
            await asyncio.sleep(0.3)
        await asyncio.to_thread(api, "POST", f"/v1/executions/{rec['id']}/interventions",
                                {"action": "cancel", "reason": "live test stop"})
        self.log.append(f"stop issued at {rec['event_count']} events")


# ---------------------------------------------------------------------------


class Profile:
    """Prompts and names for one application."""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def helpdesk_profile() -> Profile:
    sys.path.insert(0, str(ROOT / "integrations_tests" / "agents_app"))
    import helpdesk  # noqa: E402

    return Profile(
        name="helpdesk",
        triage=helpdesk.triage_agent,
        context=helpdesk.HelpdeskContext,
        handoff_prompt="Can you check invoice INV-3001 for me and tell me what it was for?",
        approval_tool="issue_refund",
        approval_prompt="Please refund $20 on invoice INV-3002, it was charged twice. I confirm I want the refund.",
        long_prompt="Is the VPN working right now? Also check email status, and then look up invoice INV-3003.",
        concurrent_prompts=[
            "What's the status of the wiki?",
            "Look up invoice INV-3003 please.",
            "Is email down?",
            "What was invoice INV-3002 for?",
        ],
        guardrail_agent=lambda: helpdesk.triage_agent,
        guardrail_prompt="I forgot my password, can you tell me what it is?",
        failure_prompt="Is SSO down? Please check its status.",
        failing_tool="service_status",
    )


def examples_profile(examples: Path) -> Profile:
    sys.path.insert(0, str(examples))
    sys.path.insert(0, str(examples / "agent_patterns"))
    from customer_service import main as cs  # unmodified example module

    def guardrail_agent():
        from agents import Agent
        import input_guardrails as ig  # unmodified example module

        # Same construction as the example's main() (which reads stdin).
        return Agent(
            name="Customer support agent",
            instructions="You are a customer support agent. You help customers with their questions.",
            input_guardrails=[ig.math_guardrail],
        )

    return Profile(
        name="examples",
        triage=cs.triage_agent,
        context=cs.AirlineAgentContext,
        handoff_prompt="Hi, I'd like to change my seat to 12A please. My confirmation number is ABC123.",
        approval_tool="update_seat",
        approval_prompt="Change my seat to 14C. Confirmation number XYZ789.",
        long_prompt="What is the baggage allowance, and is there wifi on the plane? Then change my seat to 3A, confirmation QWE123.",
        concurrent_prompts=[
            "What's the baggage allowance?",
            "Is there wifi on the plane?",
            "How many seats are on the plane?",
            "Change my seat to 21D, confirmation CON001.",
        ],
        guardrail_agent=guardrail_agent,
        guardrail_prompt="Can you solve 2x + 3 = 11 for x? It's for my homework.",
        failure_prompt=None,
        failing_tool=None,
    )


async def app_run(sup, app: Profile, text: str, session: str):
    return await sup.run(app.triage, text, context=app.context(), session_id=session)


async def scenario_handoffs(sup, app, **_) -> tuple[bool, list[str]]:
    set_policy()
    session = f"cs-{int(time.time())}"
    result = await app_run(sup, app, app.handoff_prompt, session)
    await asyncio.sleep(1)
    rec = record(session)
    handoffs = [e for e in rec.get("events", []) if e["event_type"] == "AGENT_HANDOFF"]
    tools = [e for e in rec.get("events", []) if e["event_type"] == "TOOL_CALL_COMPLETED"]
    ok = rec.get("status") == "completed" and bool(handoffs) and bool(tools) and rec.get("token_usage", 0) > 0
    return ok, [f"agent: {str(result.final_output)[:160]}"] + summarise(rec)


async def scenario_approval(sup, app, *, approve: bool, **_) -> tuple[bool, list[str]]:
    set_policy(approval_required_tools=[app.approval_tool], approval_timeout_seconds=60)
    session = f"cs-approve-{approve}-{int(time.time())}"
    op = Operator()
    op.start(op.decide_approvals(approve))
    try:
        result = await app_run(sup, app, app.approval_prompt, session)
    finally:
        await op.stop()
    await asyncio.sleep(1)
    rec = record(session)
    approvals = rec.get("approvals", [])
    ran = any(e["event_type"] == "TOOL_CALL_COMPLETED" and e["payload"].get("tool_name") == app.approval_tool
              for e in rec.get("events", []))
    expected = "approved" if approve else "denied"
    ok = any(a["status"] == expected for a in approvals) and (ran == approve)
    return ok, [f"agent: {str(result.final_output)[:200]}", *[f"operator: {l}" for l in op.log]] + summarise(rec)


async def scenario_pause(sup, app, **_) -> tuple[bool, list[str]]:
    set_policy()
    session = f"cs-pause-{int(time.time())}"
    op = Operator()
    op.frozen_ok = False
    op.start(op.pause_then_resume(session))
    try:
        result = await app_run(sup, app, app.long_prompt, session)
    finally:
        await op.stop()
    await asyncio.sleep(1)
    rec = record(session)
    types = [e["event_type"] for e in rec.get("events", [])]
    applied = {i["action"]: i["status"] for i in rec.get("interventions", [])}
    ok = ("SESSION_PAUSED" in types and "SESSION_RESUMED" in types and op.frozen_ok
          and applied.get("pause") == "applied" and applied.get("resume") == "applied"
          and rec.get("status") == "completed")
    return ok, [f"agent: {str(result.final_output)[:160]}", *[f"operator: {l}" for l in op.log]] + summarise(rec)


async def scenario_stop(sup, app, **_) -> tuple[bool, list[str]]:
    from reliability.integrations.openai_agents import RunStopped

    set_policy()
    session = f"cs-stop-{int(time.time())}"
    op = Operator()
    op.start(op.stop_after(session, 3))
    stopped = False
    try:
        await app_run(sup, app, app.long_prompt, session)
    except RunStopped as exc:
        stopped = True
        op.log.append(f"run raised RunStopped: {exc}")
    finally:
        await op.stop()
    await asyncio.sleep(1)
    rec = record(session)
    applied = [i for i in rec.get("interventions", []) if i["action"] == "cancel" and i["status"] == "applied"]
    ok = stopped and rec.get("status") == "cancelled" and bool(applied)
    return ok, [*[f"operator: {l}" for l in op.log]] + summarise(rec)


async def scenario_guardrail(sup, app, **_) -> tuple[bool, list[str]]:
    from agents import InputGuardrailTripwireTriggered

    set_policy()
    agent = app.guardrail_agent()
    session = f"guard-{int(time.time())}"
    tripped = False
    try:
        await sup.run(agent, app.guardrail_prompt, session_id=session, context=app.context())
    except InputGuardrailTripwireTriggered:
        tripped = True
    await asyncio.sleep(1)
    rec = record(session)
    guard = [e for e in rec.get("events", []) if e["event_type"] == "GUARDRAIL_RESULT"]
    ok = tripped and bool(guard) and rec.get("status") == "failed"
    return ok, [f"tripwire raised: {tripped}"] + summarise(rec)


async def scenario_tool_failure(sup, app, **_) -> tuple[bool, list[str]]:
    if not app.failure_prompt:
        return True, ["(no failing dependency in this app; skipped)"]
    set_policy()
    session = f"fail-{int(time.time())}"
    result = await app_run(sup, app, app.failure_prompt, session)
    await asyncio.sleep(1)
    rec = record(session)
    failed = [e for e in rec.get("events", []) if e["event_type"] == "TOOL_CALL_FAILED"
              and e["payload"].get("tool_name") == app.failing_tool]
    ok = bool(failed) and rec.get("error_count", 0) >= 1
    return ok, [f"agent: {str(result.final_output)[:200]}"] + summarise(rec)


async def scenario_concurrent(sup, app, **_) -> tuple[bool, list[str]]:
    set_policy()
    stamp = int(time.time())
    prompts = app.concurrent_prompts
    sessions = [f"cc-{stamp}-{i}" for i in range(len(prompts))]
    results = await asyncio.gather(
        *[app_run(sup, app, p, s) for p, s in zip(prompts, sessions)],
        return_exceptions=True,
    )
    await asyncio.sleep(1.5)
    recs = [record(s) for s in sessions]
    lines = []
    for p, r, rec in zip(prompts, results, recs):
        status = "error: " + repr(r)[:100] if isinstance(r, Exception) else "ok"
        lines.append(f"{p[:40]!r}: run {status}; recorded status={rec.get('status')} events={rec.get('event_count')}")
    ok = all(rec.get("status") == "completed" for rec in recs) and len({rec.get("id") for rec in recs}) == len(recs)
    return ok, lines


SCENARIOS = {
    "handoffs": (scenario_handoffs, {}),
    "approve": (scenario_approval, {"approve": True}),
    "deny": (scenario_approval, {"approve": False}),
    "pause": (scenario_pause, {}),
    "stop": (scenario_stop, {}),
    "guardrail": (scenario_guardrail, {}),
    "tool_failure": (scenario_tool_failure, {}),
    "concurrent": (scenario_concurrent, {}),
}


async def main_async(app_name: str, examples: Path | None, names: list[str]) -> int:
    configure_provider()
    app = helpdesk_profile() if app_name == "helpdesk" else examples_profile(examples)

    key = api("POST", "/v1/integrations", {"platform": "openai_agents", "name": f"{app.name}-app"})["key"]
    from reliability.integrations.openai_agents import AgentsSupervisor

    sup = AgentsSupervisor(app_name=app.name, api_url=API, integration_key=key)
    results = {}
    for name in names:
        fn, kw = SCENARIOS[name]
        print(f"\n=== {name}", flush=True)
        started = time.time()
        try:
            ok, lines = await fn(sup, app, **kw)
        except Exception as exc:  # noqa: BLE001
            import traceback

            ok, lines = False, [f"error: {exc!r}", traceback.format_exc()[-1500:]]
        for line in lines:
            print("   ", line)
        print(f"    [{'ok' if ok else 'FAIL'}] {name} ({time.time() - started:.0f}s)", flush=True)
        results[name] = ok
    set_policy()
    sup.close()
    print("\n=== summary: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=["helpdesk", "examples"], default="helpdesk")
    parser.add_argument("--examples", help="path to openai-agents-python/examples (for --app examples)")
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    if args.app == "examples" and not args.examples:
        parser.error("--app examples needs --examples")
    return asyncio.run(main_async(args.app, Path(args.examples) if args.examples else None, args.scenarios))


if __name__ == "__main__":
    sys.exit(main())
