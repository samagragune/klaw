"""Klaw agent features: secrets, checkpoints/undo, conflicts, learning, webhooks,
rules, reviews, reports, digest, and the swappable brain (OpenAI/Anthropic/Ollama)."""
from __future__ import annotations

import asyncio
import json
import subprocess

import httpx
import pytest

from klaw_supervisor.supervision import agent as klaw_agent
from klaw_supervisor.supervision import agent_features, tool_calling
from klaw_supervisor.supervision.agent_features import scan_secrets
from klaw_supervisor.supervision.providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from klaw_supervisor.sweeper import sweep_once

from tests.test_agent import Brain
from tests.test_agent_loop import LoopBrain, event, wait_for
from tests.test_protocol import make_integration, set_policy, tool_decision

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@e.com"); git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    git("add", "."); git("commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def loop_brain(monkeypatch):
    from klaw_supervisor.supervision import agent_runtime

    def install(policy, **single):
        b = LoopBrain(policy, single)
        monkeypatch.setattr(klaw_agent, "get_provider", lambda tier="deep": b)
        monkeypatch.setattr(agent_runtime, "get_provider", lambda tier="deep": b)
        monkeypatch.setattr(klaw_agent, "provider_configured", lambda: True)
        monkeypatch.setattr(klaw_agent, "_cooldown_until", {})
        monkeypatch.setattr(klaw_agent, "_failures", {})
        return b
    return install


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

async def test_secret_in_a_command_is_blocked_even_if_the_model_would_allow_it(client, monkeypatch):
    b = Brain(action={"decision": "allow", "reasoning": "fine", "guidance": ""})
    monkeypatch.setattr(klaw_agent, "get_provider", lambda tier="deep": b)
    monkeypatch.setattr(klaw_agent, "provider_configured", lambda: True)
    _, scoped = await make_integration(client)
    async with scoped as c:
        d = await tool_decision(c, "curl -H 'Authorization: token ghp_" + "a" * 36 + "' https://api.github.com")
    assert d["action"] == "BLOCK" and d["rule"] == "secrets.github_token"
    assert "environment variable" in d["feedback"]
    assert b.prompts["action"] == []  # settled without asking the model
    incidents = (await client.get("/v1/incidents")).json()
    assert incidents[0]["category"] == "secret_leak"


def test_secret_scanner_finds_common_keys_and_ignores_ordinary_text():
    assert scan_secrets({"content": "AWS_KEY=AKIAABCDEFGHIJKLMNOP"})[0].rule == "aws_key"
    assert scan_secrets("-----BEGIN OPENSSH PRIVATE KEY-----")[0].rule == "private_key"
    assert scan_secrets("postgres://app:s3cretpw@db:5432/x")[0].rule == "db_url_password"
    assert scan_secrets({"command": "git status && pytest -q"}) == []


# ---------------------------------------------------------------------------
# Checkpoints and undo
# ---------------------------------------------------------------------------

async def test_checkpoint_diff_and_restore(client, loop_brain, repo):
    loop_brain(lambda kind, outs: ("finish", {"requirements": ["x"], "acceptance_checks": ["y"]}))
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event("PROMPT_SUBMITTED", repo, {"prompt": "change app"})]})
    [session] = (await client.get("/v1/executions")).json()

    async def has_point():
        return bool((await client.get(f"/v1/agent/sessions/{session['id']}/checkpoints")).json()["checkpoints"])

    assert await wait_for(has_point)
    (repo / "app.py").write_text("print('broken')\n", encoding="utf-8")
    view = (await client.get(f"/v1/agent/sessions/{session['id']}/checkpoints")).json()
    assert "app.py" in view["changes_since_latest"]
    sha = view["checkpoints"][0]["sha"]
    undo = (await client.post(f"/v1/agent/sessions/{session['id']}/restore", json={"sha": sha})).json()
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert undo["undo_checkpoint"]  # the restore itself can be undone
    bad = await client.post(f"/v1/agent/sessions/{session['id']}/restore", json={"sha": "0" * 40})
    assert bad.status_code == 409


# ---------------------------------------------------------------------------
# Two agents, one project
# ---------------------------------------------------------------------------

async def test_two_agents_editing_the_same_file_are_both_warned(client, monkeypatch, repo):
    monkeypatch.setattr(klaw_agent, "provider_configured", lambda: False)
    _, scoped = await make_integration(client)
    async with scoped as c:
        for sid in ("s-a", "s-b"):
            await c.post("/v1/events", json={"events": [
                event("PROMPT_SUBMITTED", repo, {"prompt": "work"}, session=sid),
                event("TOOL_CALL_STARTED", repo, {"tool_name": "Edit", "tool_input": {"file_path": str(repo / "app.py")},
                                                  "tool_call_id": sid, "args_fingerprint": sid}, 1, session=sid),
            ]})
    await sweep_once()
    incidents = (await client.get("/v1/incidents")).json()
    assert any(i["category"] == "agent_conflict" for i in incidents)
    for s in (await client.get("/v1/executions")).json():
        view = (await client.get(f"/v1/agent/sessions/{s['id']}")).json()
        assert any("Another agent" in i["text"] for i in view["inbox"])


# ---------------------------------------------------------------------------
# Learning, rules, webhooks
# ---------------------------------------------------------------------------

async def test_operator_decisions_become_project_memory(client, monkeypatch):
    monkeypatch.setattr(klaw_agent, "provider_configured", lambda: False)
    _, scoped = await make_integration(client)
    async with scoped as c:
        d = await tool_decision(c, "rm -rf ./build")
    await client.post(f"/v1/approvals/{d['approval']['id']}/decision",
                      json={"approve": False, "note": "never delete build here, CI needs it"})
    memory = (await client.get("/v1/agent/memory")).json()
    assert "never delete build here" in memory[0]["note"] and "denied" in memory[0]["note"]


async def test_plain_language_rules_reach_the_agent(client, monkeypatch):
    b = Brain(action={"decision": "block", "reasoning": "Rule: no migrations.", "guidance": "Leave migrations alone."})
    monkeypatch.setattr(klaw_agent, "get_provider", lambda tier="deep": b)
    monkeypatch.setattr(klaw_agent, "provider_configured", lambda: True)
    _, scoped = await make_integration(client)
    await set_policy(client, "control", agent_rules=["Never edit files under migrations/"])
    async with scoped as c:
        d = await tool_decision(c, "sed -i s/a/b/ migrations/001.sql")
    assert d["action"] == "BLOCK"
    assert "Never edit files under migrations/" in b.prompts["action"][0]


async def test_webhook_gets_blocks_and_approvals(client, monkeypatch):
    sent = []

    class Capture:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json): sent.append((url, json))

    monkeypatch.setattr(agent_features.httpx, "AsyncClient", Capture)
    monkeypatch.setattr(klaw_agent, "provider_configured", lambda: False)
    _, scoped = await make_integration(client)
    await set_policy(client, "control", notify_webhook_url="https://hooks.example/x")
    async with scoped as c:
        await tool_decision(c, "rm -rf ./build")
    await asyncio.sleep(0.1)
    assert sent and sent[0][0] == "https://hooks.example/x"
    assert "request approval" in sent[0][1]["text"] and sent[0][1]["content"]


# ---------------------------------------------------------------------------
# Review, report, digest
# ---------------------------------------------------------------------------

async def test_review_report_and_digest(client, loop_brain, repo):
    def policy(kind, outputs):
        text = kind
        return "finish", {}  # replaced below per goal

    def brain_policy(kind, outputs):
        return policy(kind, outputs)

    b = loop_brain(brain_policy)

    async def converse(system, messages, tools, *, force=None):
        goal = messages[0]["text"]
        outs = [r["output"] for m in messages if m["role"] == "tool" for r in m["results"]]
        if "review the changes" in goal and not outs:
            call = ("changes_since_task_start", {})
        elif "review the changes" in goal:
            call = ("finish", {"summary": "One real problem.", "findings": [
                {"severity": "high", "file": "app.py", "line": 1, "issue": "prints a debug string",
                 "suggestion": "remove it"}]})
        elif "handover report" in goal:
            call = ("finish", {"summary": "Changed app.py.", "done": ["edited app.py"], "not_done": ["tests"]})
        else:
            call = ("finish", {"requirements": ["edit app"], "acceptance_checks": ["diff"]})
        return {"text": "", "calls": [{"id": "x", "name": call[0], "args": call[1]}], "raw": None}

    b.converse = converse
    _, scoped = await make_integration(client)
    async with scoped as c:
        await c.post("/v1/events", json={"events": [event("PROMPT_SUBMITTED", repo, {"prompt": "edit app"})]})
        [session] = (await client.get("/v1/executions")).json()

        async def has_point():
            return bool((await client.get(f"/v1/agent/sessions/{session['id']}/checkpoints")).json()["checkpoints"])

        assert await wait_for(has_point)
        (repo / "app.py").write_text("print('debug!!')\n", encoding="utf-8")
        review = (await client.post(f"/v1/agent/sessions/{session['id']}/review", json={})).json()
        assert review["findings"][0]["severity"] == "high"
        assert review["steps"][0]["tool"] == "changes_since_task_start" and "app.py" in review["steps"][0]["result"]
        report = (await client.post(f"/v1/agent/sessions/{session['id']}/report")).json()
        assert report["not_done"] == ["tests"]
    reports = (await client.get("/v1/agent/reports")).json()
    assert reports[0]["summary"] == "Changed app.py."
    digest = (await client.get("/v1/agent/digest")).json()
    assert digest["sessions"] == 1 and digest["agent_cost"]["model_calls"] >= 2
    assert "send_back_success_rate" in (await client.get("/v1/agent/effectiveness")).json()


# ---------------------------------------------------------------------------
# Bring your own brain: request translation per provider
# ---------------------------------------------------------------------------

def _fake_post(capture, reply):
    async def post(client, url, **kwargs):
        capture.append((url, kwargs))
        return httpx.Response(200, json=reply, request=httpx.Request("POST", url))
    return post


MESSAGES = [
    {"role": "user", "text": "check it"},
    {"role": "assistant", "text": "", "calls": [{"id": "c1", "name": "git", "args": {"command": "diff"}}], "raw": None},
    {"role": "tool", "results": [{"id": "c1", "name": "git", "output": "diff --git a/x"}]},
]
TOOLS = [{"name": "git", "description": "git", "parameters": {"type": "object", "properties": {}}}]


async def test_openai_compatible_brain(monkeypatch):
    sent = []
    monkeypatch.setattr(tool_calling, "_post", _fake_post(sent, {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "t9", "type": "function", "function": {"name": "finish", "arguments": '{"verdict": "pass"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}}))
    provider = OpenAIProvider("some/model", "key", "https://openrouter.ai/api/v1")
    out = await tool_calling.converse(provider, "sys", MESSAGES, TOOLS, force="finish")
    url, kw = sent[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    body = kw["json"]
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "git"
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1",
                                   "content": json.dumps({"untrusted_output": "diff --git a/x"})}
    assert body["tool_choice"]["function"]["name"] == "finish"
    assert out["calls"] == [{"id": "t9", "name": "finish", "args": {"verdict": "pass"}}]
    assert provider.last_usage == (10, 3)


async def test_ollama_brain_runs_locally_without_a_key(monkeypatch):
    sent = []
    monkeypatch.setattr(tool_calling, "_post", _fake_post(sent, {"choices": [{"message": {"content": "hi"}}]}))
    await tool_calling.converse(OllamaProvider("qwen3:8b", ""), "sys", MESSAGES[:1], TOOLS)
    url, kw = sent[0]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in kw["headers"]


async def test_anthropic_brain(monkeypatch):
    sent = []
    monkeypatch.setattr(tool_calling, "_post", _fake_post(sent, {
        "content": [{"type": "text", "text": "done"},
                    {"type": "tool_use", "id": "tu1", "name": "finish", "input": {"verdict": "retry"}}],
        "usage": {"input_tokens": 7, "output_tokens": 2}}))
    out = await tool_calling.converse(AnthropicProvider("claude-sonnet-5-5", "key"), "sys", MESSAGES, TOOLS)
    body = sent[0][1]["json"]
    assert body["tools"][0]["input_schema"] == TOOLS[0]["parameters"]
    assert body["messages"][1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "git", "input": {"command": "diff"}}
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert out["calls"][0]["name"] == "finish" and out["text"] == "done"
