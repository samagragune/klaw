"""The Klaw agent's own loop: it uses tools, reads real evidence, and acts.

The model is scripted, but everything else is real: a git repository on
disk, the diff the agent reads, the allowlisted check it runs, the guidance
and stops it issues. The scripted model decides from the tool output it is
given, so these tests prove evidence actually flows back into decisions.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import pytest

from klaw_supervisor.supervision import agent as klaw_agent
from klaw_supervisor.supervision import agent_runtime
from klaw_supervisor.supervision.agent_tools import ToolContext, _read_file, _run_check
from klaw_supervisor.supervision.providers import LLMProvider
from klaw_supervisor.sweeper import sweep_once

from tests.test_protocol import make_integration, set_policy

pytestmark = pytest.mark.asyncio


class LoopBrain(LLMProvider):
    """A tool-using model. ``policy(kind, transcript)`` returns the next call."""

    name = "scripted-loop"
    supports_tools = True

    def __init__(self, policy, single=None) -> None:
        super().__init__("scripted-model", "")
        self.policy = policy
        self.single = single or {}
        self.seen: list[str] = []  # every tool output the model was shown

    @staticmethod
    def _kind(system: str, contents) -> str:
        text = contents[0]["parts"][0]["text"]
        for marker, kind in (("claims the task is finished", "verify"), ("new task has started", "plan"),
                             ("THE OPERATOR", "chat"), ("YOUR FIRST LOOK", "monitor")):
            if marker in text:
                return kind
        return "other"

    async def converse(self, system, contents, tools, *, force=None):
        outputs = [
            part["functionResponse"]["response"]["untrusted_output"]
            for message in contents if message["role"] == "user"
            for part in message["parts"] if "functionResponse" in part
        ]
        self.seen = outputs
        name, args = self.policy(self._kind(system, contents), outputs)
        self.last_usage = (300, 30)
        return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}}]}

    async def complete(self, system: str, prompt: str) -> str:  # quick single-shot checks
        self.last_usage = (100, 10)
        if system == klaw_agent.MONITOR_SYSTEM:
            return json.dumps(self.single.get("monitor") or {"assessment": "on_track", "action": "none", "reasoning": "ok"})
        return json.dumps(self.single.get("action") or {"decision": "allow", "reasoning": "ok", "guidance": ""})


@pytest.fixture
def loop_brain(monkeypatch):
    def install(policy, **single) -> LoopBrain:
        b = LoopBrain(policy, single)
        monkeypatch.setattr(klaw_agent, "get_provider", lambda tier="deep": b)
        monkeypatch.setattr(agent_runtime, "get_provider", lambda tier="deep": b)
        monkeypatch.setattr(klaw_agent, "provider_configured", lambda: True)
        monkeypatch.setattr(klaw_agent, "_cooldown_until", {})
        monkeypatch.setattr(klaw_agent, "_failures", {})
        return b

    return install


@pytest.fixture
def repo(tmp_path):
    """A real git repository where the supervised agent 'worked'."""
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("This is teh app.\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"version": "1.4.0"}\n', encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    git("add", "README.md", "package.json")
    git("commit", "-qm", "init")
    # What the supervised agent did: fixed the typo AND bumped the version.
    (tmp_path / "README.md").write_text("This is the app.\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"version": "2.0.0"}\n', encoding="utf-8")
    return tmp_path


def event(event_type, cwd, payload=None, offset=0.0, session="s-loop"):
    return {
        "event_id": uuid.uuid4().hex,
        "platform": "claude_code",
        "agent_id": "cc@host:proj",
        "session_id": session,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_context": {"cwd": str(cwd)},
        "payload": payload or {},
    }


async def wait_for(predicate, tries=80):
    for _ in range(tries):
        if await predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Verification at completion
# ---------------------------------------------------------------------------

async def test_agent_verifies_a_claim_against_the_real_diff_and_sends_work_back(client, loop_brain, repo):
    def policy(kind, outputs):
        if kind == "plan":
            return "finish", {"requirements": ["fix typo in README.md", "change no other file"],
                              "acceptance_checks": ["diff touches only README.md"]}
        if kind != "verify":
            return "finish", {"answer": "ok"}
        if not outputs:
            return "git", {"command": "diff"}
        if len(outputs) == 1:
            return "run_check", {"name": "tests"}
        diff, check = outputs[0], outputs[1]
        if "package.json" in diff:
            return "finish", {
                "verdict": "retry", "confidence": "high",
                "reasoning": "The diff also changes package.json, which the user forbade.",
                "evidence": ["git diff: package.json version 1.4.0 -> 2.0.0", check.splitlines()[0]],
                "missing": ["Revert package.json"],
                "feedback": "Revert the change to package.json; only README.md may change.",
            }
        return "finish", {"verdict": "pass", "confidence": "high", "reasoning": "Only README changed."}

    brain = loop_brain(policy)
    _, scoped = await make_integration(client)
    await set_policy(client, "control", agent_checks={"tests": f'"{sys.executable}" -c "print(42)"'})
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event(
            "PROMPT_SUBMITTED", repo, {"prompt": "Fix the typo in README.md. Do not change any other file."})]})
        stop = await c.post("/v1/decisions", json={"event": event(
            "TASK_COMPLETED", repo, {"output": "Fixed the typo. All tests pass."}, 1)})
    decision = stop.json()
    assert decision["action"] == "REDIRECT", decision
    assert decision["rule"] == "agent.completion.retry"
    assert "Revert the change to package.json" in decision["feedback"]
    # The evidence was real: the diff and the check output reached the model.
    assert any("2.0.0" in out for out in brain.seen)
    assert any("exit code 0" in out and "42" in out for out in brain.seen)
    [session] = (await client.get("/v1/executions")).json()
    view = (await client.get(f"/v1/agent/sessions/{session['id']}")).json()
    verify = [i for i in view["investigations"] if i["purpose"] == "completion"][-1]
    assert [s["tool"] for s in verify["steps"]] == ["git", "run_check"]


async def test_a_clean_diff_passes(client, loop_brain, repo):
    subprocess.run(["git", "checkout", "--", "package.json"], cwd=repo, check=True)

    def policy(kind, outputs):
        if kind == "verify" and not outputs:
            return "git", {"command": "diff"}
        if kind == "verify":
            ok = "package.json" not in outputs[0]
            return "finish", {"verdict": "pass" if ok else "retry", "confidence": "high", "reasoning": outputs[0][:80]}
        return "finish", {"requirements": [], "acceptance_checks": []}

    loop_brain(policy)
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event("PROMPT_SUBMITTED", repo, {"prompt": "Fix the typo"})]})
        decision = (await c.post("/v1/decisions", json={"event": event(
            "TASK_COMPLETED", repo, {"output": "Fixed."}, 1)})).json()
    assert decision["action"] == "ALLOW" and decision["rule"] == "agent.completion.pass"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

async def test_agent_plans_a_new_task_by_looking_at_the_project(client, loop_brain, repo):
    def policy(kind, outputs):
        if kind == "plan" and not outputs:
            return "list_dir", {"path": "."}
        if kind == "plan":
            assert "README.md" in outputs[0]
            return "finish", {"requirements": ["Fix typo in README.md", "Touch no other file"],
                              "acceptance_checks": ["git diff shows only README.md"],
                              "risks": ["editing package.json"], "briefing": "Only README.md may change."}
        return "finish", {"answer": "ok"}

    loop_brain(policy)
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event("PROMPT_SUBMITTED", repo, {"prompt": "Fix the typo in README.md only"})]})
    [session] = (await client.get("/v1/executions")).json()

    async def planned():
        view = (await client.get(f"/v1/agent/sessions/{session['id']}")).json()
        return any(p.get("requirements") for p in view["plans"].values())

    assert await wait_for(planned)
    view = (await client.get(f"/v1/agent/sessions/{session['id']}")).json()
    plan = next(iter(view["plans"].values()))
    assert "Touch no other file" in plan["requirements"]
    # The briefing reaches the supervised agent at its next step.
    assert any("Only README.md" in i["text"] for i in view["inbox"])


# ---------------------------------------------------------------------------
# Talking to the agent, and it acting
# ---------------------------------------------------------------------------

async def test_operator_asks_the_agent_to_stop_a_session_and_it_does(client, loop_brain, repo):
    def policy(kind, outputs):
        if kind == "chat" and not outputs:
            return "session_history", {"limit": 10}
        if kind == "chat" and len(outputs) == 1:
            return "stop_session", {"reason": "The operator asked to stop."}
        if kind == "chat":
            return "finish", {"answer": "Stopped. It had only read the README."}
        return "finish", {"requirements": [], "acceptance_checks": []}

    loop_brain(policy)
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event("PROMPT_SUBMITTED", repo, {"prompt": "Refactor"})]})
        reply = (await client.post("/v1/agent/chat", json={"message": "Stop this session now."})).json()
        assert reply["answer"].startswith("Stopped")
        assert reply["effects"][0]["type"] == "stop"
        nxt = (await c.post("/v1/decisions", json={"event": event(
            "TOOL_CALL_STARTED", repo, {"tool_name": "Bash", "tool_input": {"command": "ls"},
                                        "tool_call_id": "t1", "args_fingerprint": "f"}, 2)})).json()
    assert nxt["action"] == "STOP"


async def test_agent_investigates_a_worrying_session_and_guides_it(client, loop_brain, repo):
    def policy(kind, outputs):
        if kind == "monitor" and not outputs:
            return "git", {"command": "status"}
        if kind == "monitor" and len(outputs) == 1:
            return "send_guidance", {"message": "package.json is modified; revert it."}
        if kind == "monitor":
            return "finish", {"assessment": "drifting", "reasoning": "Edited package.json."}
        return "finish", {"requirements": [], "acceptance_checks": []}

    loop_brain(policy, monitor={"assessment": "drifting", "action": "guide", "reasoning": "Off task?", "guidance": ""})
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [
            event("PROMPT_SUBMITTED", repo, {"prompt": "Fix README typo only"}),
            event("TOOL_CALL_STARTED", repo, {"tool_name": "Edit", "tool_input": {"file_path": "package.json"},
                                              "tool_call_id": "e1", "args_fingerprint": "x"}, 1),
        ]})
        await asyncio.sleep(0.3)  # let the background plan finish
        await sweep_once()
        nxt = (await c.post("/v1/decisions", json={"event": event(
            "TOOL_CALL_STARTED", repo, {"tool_name": "Bash", "tool_input": {"command": "ls"},
                                        "tool_call_id": "t2", "args_fingerprint": "g"}, 2)})).json()
    assert nxt["action"] == "WARN"
    assert "revert it" in nxt["feedback"]


# ---------------------------------------------------------------------------
# Tool safety
# ---------------------------------------------------------------------------

def _ctx(repo, checks=None):
    class E:
        context = {"cwd": str(repo)}
    return ToolContext(session=None, execution=E(), task=None, config={"agent_checks": checks or {}},
                       mode="control", capabilities=set())


async def test_tools_refuse_secrets_escapes_and_unlisted_commands(repo):
    ctx = _ctx(repo)
    with pytest.raises(PermissionError):
        await _read_file(ctx, {"path": ".env"})
    with pytest.raises(PermissionError):
        await _read_file(ctx, {"path": "../../etc/passwd"})
    assert "teh" not in await _read_file(ctx, {"path": "README.md"})
    out = await _run_check(ctx, {"name": "rm -rf /"})
    assert "not an allowed check" in out
