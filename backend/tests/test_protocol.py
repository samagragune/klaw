"""Universal protocol, decision points, approvals, capabilities and accounts.

These exercise the backend contract adapters rely on. The adapters themselves
are tested against real Claude Code / Agents SDK runs separately; nothing here
stands in for that.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.supervision import output_review
from app.supervision.providers import LLMProvider

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


def ev(event_type: str, *, session="s-1", agent="cc@host:proj", platform="claude_code",
       payload=None, task_id=None, offset=0.0, event_id=None) -> dict:
    return {
        "event_id": event_id or uuid.uuid4().hex,
        "platform": platform,
        "agent_id": agent,
        "session_id": session,
        "event_type": event_type,
        "timestamp": (NOW + timedelta(seconds=offset)).isoformat(),
        "task_id": task_id,
        "execution_context": {"cwd": "/work/proj"},
        "payload": payload or {},
    }


class Scoped:
    """An integration-key client that can be entered more than once."""

    def __init__(self, key: str) -> None:
        self.key = key
        self._client: AsyncClient | None = None

    async def __aenter__(self) -> AsyncClient:
        self._client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {self.key}"},
        )
        return await self._client.__aenter__()

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)


async def make_integration(client, platform="claude_code", name="laptop") -> tuple[dict, AsyncClient]:
    response = await client.post("/v1/integrations", json={"platform": platform, "name": name})
    assert response.status_code == 201
    body = response.json()
    return body, Scoped(body["key"])


async def set_policy(client, mode: str, **config) -> None:
    response = await client.put(
        "/v1/policies", json={"supervision_mode": mode, "configuration": config}
    )
    assert response.status_code == 200, response.text


async def tool_decision(scoped_client, command: str, *, call_id=None, session="s-1",
                        platform="claude_code", tool="Bash", fingerprint=None) -> dict:
    payload = {
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_call_id": call_id or uuid.uuid4().hex,
        "args_fingerprint": fingerprint or f"fp-{command}",
    }
    response = await scoped_client.post(
        "/v1/decisions",
        json={"event": ev("TOOL_CALL_STARTED", session=session, platform=platform, payload=payload)},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Integrations and scoped keys
# ---------------------------------------------------------------------------

async def test_integration_key_is_scoped_to_events_and_decisions(client):
    integration, scoped = await make_integration(client)
    assert integration["key"].startswith("rl_int_")
    assert "request_approval" in integration["capabilities"]
    async with scoped as scoped_client:
        assert (await scoped_client.get("/v1/agents")).status_code == 403
        assert (await scoped_client.get("/v1/policies")).status_code == 403
        ok = await scoped_client.post("/v1/events", json={"events": [ev("SESSION_STARTED")]})
        assert ok.status_code == 200


async def test_integration_key_cannot_speak_for_another_platform(client):
    _, scoped = await make_integration(client, platform="claude_code")
    async with scoped as scoped_client:
        response = await scoped_client.post(
            "/v1/events", json={"events": [ev("SESSION_STARTED", platform="openai_agents")]}
        )
    assert response.status_code == 422


async def test_connect_cannot_claim_capabilities_the_platform_lacks(client):
    _, scoped = await make_integration(client, platform="claude_code")
    async with scoped as scoped_client:
        response = await scoped_client.post(
            "/v1/integrations/connect",
            json={"capabilities": ["observe", "block_tool", "pause"], "details": {"version": "1"}},
        )
    assert response.status_code == 200
    assert "pause" not in response.json()["capabilities"]
    assert "block_tool" in response.json()["capabilities"]


async def test_revoked_integration_stops_working(client):
    integration, scoped = await make_integration(client)
    assert (await client.delete(f"/v1/integrations/{integration['id']}")).status_code == 204
    async with scoped as scoped_client:
        response = await scoped_client.post("/v1/events", json={"events": [ev("SESSION_STARTED")]})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

async def test_events_self_register_agent_session_and_task(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post(
            "/v1/events",
            json={
                "events": [
                    ev("SESSION_STARTED", offset=0),
                    ev("PROMPT_SUBMITTED", payload={"prompt": "Fix the failing test"}, offset=1),
                    ev("TOOL_CALL_STARTED", payload={"tool_name": "Bash", "tool_call_id": "t1"}, offset=2),
                    ev("TOOL_CALL_COMPLETED", payload={"tool_name": "Bash", "tool_call_id": "t1"}, offset=3),
                    ev("TASK_COMPLETED", payload={"output": "Fixed it."}, offset=4),
                ]
            },
        )
    agents = (await client.get("/v1/agents")).json()
    assert [a["platform"] for a in agents] == ["claude_code"]
    sessions = (await client.get("/v1/executions")).json()
    assert len(sessions) == 1
    session = sessions[0]
    # Multi-task platform: finishing a turn leaves the session idle, not over.
    assert session["status"] == "idle"
    detail = (await client.get(f"/v1/executions/{session['id']}")).json()
    [task] = detail["tasks"]
    assert task["objective"] == "Fix the failing test"
    assert task["status"] == "completed"
    assert task["output_summary"] == "Fixed it."
    assert task["tool_call_count"] == 1
    assert all(e["task_id"] == task["id"] for e in detail["events"] if e["event_type"].startswith("TOOL"))


async def test_a_prompt_without_completion_is_marked_abandoned_not_done(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post(
            "/v1/events",
            json={
                "events": [
                    ev("PROMPT_SUBMITTED", payload={"prompt": "first"}, offset=0),
                    ev("PROMPT_SUBMITTED", payload={"prompt": "second"}, offset=5),
                ]
            },
        )
    tasks = (await client.get("/v1/tasks")).json()
    by_objective = {t["objective"]: t["status"] for t in tasks}
    assert by_objective == {"first": "abandoned", "second": "running"}


async def test_protocol_ingestion_is_idempotent(client):
    _, scoped = await make_integration(client)
    event = ev("TOOL_CALL_STARTED", payload={"tool_name": "Read"}, event_id="fixed-id")
    async with scoped as scoped_client:
        first = (await scoped_client.post("/v1/events", json={"events": [event]})).json()
        second = (await scoped_client.post("/v1/events", json={"events": [event]})).json()
    assert (first["accepted"], second["accepted"], second["duplicates"]) == (1, 0, 1)


async def test_concurrent_sessions_stay_separate(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await asyncio.gather(
            *[
                scoped_client.post(
                    "/v1/events",
                    json={"events": [ev("PROMPT_SUBMITTED", session=f"s-{i}", payload={"prompt": f"p{i}"})]},
                )
                for i in range(6)
            ]
        )
    sessions = (await client.get("/v1/executions")).json()
    assert len(sessions) == 6
    assert len({s["agent_id"] for s in sessions}) == 1


async def test_unknown_protocol_event_type_is_rejected(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        response = await scoped_client.post("/v1/events", json={"events": [ev("TOOL_STARTED")]})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

async def test_safe_command_is_allowed(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control")
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "pytest -q")
    assert decision["action"] == "ALLOW"


async def test_destructive_command_needs_approval_in_control_mode(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control")
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "rm -rf / --no-preserve-root")
    assert decision["action"] == "REQUEST_APPROVAL"
    assert decision["rule"].startswith("risk.fs.recursive_delete")
    assert decision["approval"]["on_timeout"] == "deny"
    incidents = (await client.get("/v1/incidents")).json()
    assert [i["category"] for i in incidents] == ["risky_operation"]


async def test_recommend_mode_warns_instead_of_refusing(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "recommend")
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "git push --force origin main")
    assert decision["action"] == "WARN"
    assert decision["intended_action"] == "REQUEST_APPROVAL"
    assert "Recommend mode" in decision["held_back_reason"]
    assert decision["feedback"]


async def test_observe_mode_never_changes_behaviour_but_records_it(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "observe")
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "git reset --hard HEAD~3")
    assert decision["action"] == "ALLOW"
    assert decision["intended_action"] == "REQUEST_APPROVAL"
    assert decision["feedback"] is None
    recorded = (await client.get("/v1/decisions")).json()
    assert recorded and recorded[0]["intended_action"] == "REQUEST_APPROVAL"


async def test_unsupported_action_is_degraded_and_says_why(client):
    # The custom SDK cannot hold a call for approval; it can block.
    integration, scoped = await make_integration(client, platform="custom_sdk")
    await set_policy(client, "control")
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "rm -rf ~", platform="custom_sdk", tool="shell")
    assert decision["action"] == "BLOCK"
    assert decision["intended_action"] == "REQUEST_APPROVAL"
    assert "cannot request approval" in decision["held_back_reason"]


async def test_loop_guard_blocks_a_call_that_keeps_returning_the_same_thing(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control", repeated_tool_call_threshold=3)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [ev("PROMPT_SUBMITTED", payload={"prompt": "check"})]})
        for i in range(3):
            call_id = f"c{i}"
            decision = await tool_decision(scoped_client, "curl http://localhost:9/health", call_id=call_id)
            assert decision["action"] == "ALLOW", f"call {i} should run"
            await scoped_client.post(
                "/v1/events",
                json={"events": [ev("TOOL_CALL_COMPLETED", offset=i + 0.5, payload={
                    "tool_name": "Bash", "tool_call_id": call_id, "result_fingerprint": "same-output"})]},
            )
        fourth = await tool_decision(scoped_client, "curl http://localhost:9/health")
    assert fourth["action"] == "BLOCK"
    assert fourth["rule"] == "loop.repeated_call"
    assert "3 times" in fourth["feedback"]


async def test_repeating_a_call_that_returns_new_results_is_not_a_loop(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control", repeated_tool_call_threshold=3)
    async with scoped as scoped_client:
        for i in range(4):
            call_id = f"c{i}"
            decision = await tool_decision(scoped_client, "pytest -q", call_id=call_id)
            assert decision["action"] == "ALLOW"
            await scoped_client.post(
                "/v1/events",
                json={"events": [ev("TOOL_CALL_COMPLETED", offset=i + 0.5, payload={
                    "tool_name": "Bash", "tool_call_id": call_id, "result_fingerprint": f"run-{i}"})]},
            )
    incidents = (await client.get("/v1/incidents")).json()
    assert not [i for i in incidents if i["category"] == "repeated_tool_call"]


async def test_policy_blocked_tool_is_refused_with_a_reason(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control", blocked_tools=["WebFetch"])
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "x", tool="WebFetch")
    assert decision["action"] == "BLOCK"
    assert decision["rule"] == "policy.blocked_tool"


async def test_operator_stop_reaches_a_claude_code_session_at_its_next_tool_call(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [ev("PROMPT_SUBMITTED", payload={"prompt": "go"})]})
    [session] = (await client.get("/v1/executions")).json()
    stop = await client.post(
        f"/v1/executions/{session['id']}/interventions",
        json={"action": "cancel", "reason": "wrong branch"},
    )
    assert stop.status_code == 201
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "ls")
    assert decision["action"] == "STOP"
    assert decision["reason"] == "wrong branch"


async def test_pausing_a_platform_that_cannot_pause_is_refused_up_front(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [ev("PROMPT_SUBMITTED", payload={"prompt": "go"})]})
    [session] = (await client.get("/v1/executions")).json()
    response = await client.post(
        f"/v1/executions/{session['id']}/interventions", json={"action": "pause"}
    )
    assert response.status_code == 409
    assert "do not support 'pause'" in response.json()["detail"]


async def test_unscreened_input_is_allowed_and_says_so(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control")
    payload = {"tool_name": "Bash", "tool_call_id": "x", "args_fingerprint": "fp"}
    async with scoped as scoped_client:
        response = await scoped_client.post(
            "/v1/decisions", json={"event": ev("TOOL_CALL_STARTED", payload=payload)}
        )
    body = response.json()
    assert body["action"] == "ALLOW"
    assert body["rule"] == "default.allow_unscreened"


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

async def test_approval_round_trip(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control", approval_required_tools=["Bash"])
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "make deploy")
        approval_id = decision["approval"]["id"]

        waiter = asyncio.create_task(scoped_client.get(f"/v1/approvals/{approval_id}/wait?wait=10"))
        await asyncio.sleep(0.2)
        queue = (await client.get("/v1/approvals?status=pending")).json()
        assert [a["id"] for a in queue] == [approval_id]
        decided = await client.post(
            f"/v1/approvals/{approval_id}/decision", json={"approve": True, "note": "ok"}
        )
        assert decided.status_code == 200
        result = (await waiter).json()
    assert result["status"] == "approved"
    # A second answer is refused: the agent already moved on.
    again = await client.post(f"/v1/approvals/{approval_id}/decision", json={"approve": False})
    assert again.status_code == 409


async def test_unanswered_approval_expires(client):
    _, scoped = await make_integration(client)
    await set_policy(client, "control", approval_required_tools=["Bash"], approval_timeout_seconds=5)
    async with scoped as scoped_client:
        decision = await tool_decision(scoped_client, "make deploy")
        approval_id = decision["approval"]["id"]
    from app.db import SessionLocal
    from app.models import ApprovalRequest

    async with SessionLocal() as session:
        row = await session.get(ApprovalRequest, approval_id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    async with scoped as scoped_client:
        result = (await scoped_client.get(f"/v1/approvals/{approval_id}/wait?wait=0")).json()
    assert result["status"] == "expired"
    assert result["decided_by"] == "timeout"


# ---------------------------------------------------------------------------
# Completion review (corrective feedback)
# ---------------------------------------------------------------------------

class Stub(LLMProvider):
    name = "stub"

    def __init__(self, verdict: dict) -> None:
        super().__init__(model="stub", api_key="")
        self.verdict = verdict
        self.calls = 0

    async def complete(self, system: str, prompt: str) -> str:
        self.calls += 1
        self.last_usage = (100, 20)
        return json.dumps(self.verdict)


async def test_incomplete_output_is_sent_back_once_with_the_gaps(client, monkeypatch):
    stub = Stub({"satisfies_objective": False, "gaps": ["No tests were run"],
                 "reasoning": "unverified", "confidence": "high"})
    monkeypatch.setattr(output_review, "get_provider", lambda tier="deep": stub)
    _, scoped = await make_integration(client)
    await set_policy(client, "control", review_output_before_completion=True, max_redirects=1)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [ev("PROMPT_SUBMITTED", payload={"prompt": "Fix the bug and run tests"})]})
        stop = {"event": ev("TASK_COMPLETED", offset=1, payload={"output": "Done!"})}
        first = (await scoped_client.post("/v1/decisions", json=stop)).json()
        stop2 = {"event": ev("TASK_COMPLETED", offset=2, payload={"output": "Done!"})}
        second = (await scoped_client.post("/v1/decisions", json=stop2)).json()
    assert first["action"] == "REDIRECT"
    assert "No tests were run" in first["feedback"]
    # Never twice for the same task: that is how correction becomes a loop.
    assert second["action"] == "ALLOW"

    costs = (await client.get("/v1/metrics/evaluation-costs")).json()
    assert costs["calls"] >= 1
    assert costs["tokens"] >= 120


async def test_evaluator_answers_identical_questions_from_cache(client, monkeypatch):
    stub = Stub({"satisfies_objective": True, "gaps": [], "reasoning": "fine", "confidence": "high"})
    monkeypatch.setattr(output_review, "get_provider", lambda tier="deep": stub)
    monkeypatch.setattr("app.routers.executions.provider_configured", lambda: True)
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [
            ev("PROMPT_SUBMITTED", payload={"prompt": "summarise"}),
            ev("TASK_COMPLETED", offset=1, payload={"output": "summary"}),
        ]})
    [session] = (await client.get("/v1/executions")).json()
    await client.post(f"/v1/executions/{session['id']}/evaluate-output")
    await client.post(f"/v1/executions/{session['id']}/evaluate-output")
    assert stub.calls == 1
    costs = (await client.get("/v1/metrics/evaluation-costs")).json()
    assert costs["cached"] == 1


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

async def test_signup_login_and_session_token(accounts):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        created = await anon.post(
            "/v1/auth/signup",
            json={"email": "Ops@Example.com", "password": "correct horse battery"},
        )
        assert created.status_code == 201
        assert (await anon.post("/v1/auth/signup", json={
            "email": "ops@example.com", "password": "another long pw"})).status_code == 409
        bad = await anon.post("/v1/auth/login", json={"email": "ops@example.com", "password": "nope"})
        assert bad.status_code == 401
        good = await anon.post(
            "/v1/auth/login", json={"email": "ops@example.com", "password": "correct horse battery"}
        )
        token = good.json()["token"]
        me = await anon.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["email"] == "ops@example.com"
        # A new account sees nothing from other tenants.
        agents = await anon.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert agents.json() == []


async def test_tampered_session_token_is_rejected(accounts):
    from app.security import issue_session_token

    token, _ = issue_session_token(accounts["primary"]["user_id"])
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.get("/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


async def test_dev_token_is_refused_outside_development(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "reliability_env", "production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.get(
            "/v1/agents", headers={"Authorization": "Bearer test-dashboard-token"}
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Regressions from live Claude Code runs
# ---------------------------------------------------------------------------

async def test_unbounded_shell_loop_is_flagged_and_bounded_ones_are_not():
    from app.supervision import risk

    def rules(command):
        return [f.rule for f in risk.assess("Bash", {"command": command})]

    # What Claude Code actually ran after two failed identical calls.
    assert rules("until curl -s -m 2 http://127.0.0.1:9/health; do sleep 1; done") == ["exec.unbounded_loop"]
    assert rules("while true; do curl x; sleep 1; done") == ["exec.unbounded_loop"]
    assert rules("until curl x || [ $i -gt 5 ]; do i=$((i+1)); sleep 1; done") == []
    assert rules('timeout 30 bash -c "until curl x; do sleep 1; done"') == []
    assert rules("while read line; do echo $line; done < f") == []


async def test_reported_stop_cancels_the_turn_but_keeps_the_session(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [
            ev("PROMPT_SUBMITTED", payload={"prompt": "five echoes"}),
            ev("AGENT_STOPPED", offset=1, payload={"reason": "operator", "by": "operator"}),
        ]})
    [session] = (await client.get("/v1/executions")).json()
    assert session["status"] == "idle"
    [task] = (await client.get("/v1/tasks")).json()
    assert task["status"] == "cancelled"


async def test_tool_call_without_outcome_is_closed_when_the_task_ends(client):
    _, scoped = await make_integration(client)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [
            ev("PROMPT_SUBMITTED", payload={"prompt": "run it"}),
            ev("TOOL_CALL_STARTED", offset=1, payload={"tool_name": "PowerShell", "tool_call_id": "t9"}),
            ev("TASK_COMPLETED", offset=2, payload={"output": "could not run it"}),
        ]})
    [session] = (await client.get("/v1/executions")).json()
    detail = (await client.get(f"/v1/executions/{session['id']}")).json()
    closed = [e for e in detail["events"] if e["event_type"] == "TOOL_CALL_BLOCKED"]
    assert len(closed) == 1 and closed[0]["payload"]["inferred"] is True


async def test_redirect_reopens_the_task(client, monkeypatch):
    stub = Stub({"satisfies_objective": False, "gaps": ["tests not run"], "confidence": "high"})
    monkeypatch.setattr(output_review, "get_provider", lambda tier="deep": stub)
    _, scoped = await make_integration(client)
    await set_policy(client, "control", review_output_before_completion=True)
    async with scoped as scoped_client:
        await scoped_client.post("/v1/events", json={"events": [ev("PROMPT_SUBMITTED", payload={"prompt": "fix and test"})]})
        decision = (await scoped_client.post("/v1/decisions", json={
            "event": ev("TASK_COMPLETED", offset=1, payload={"output": "done"})})).json()
    assert decision["action"] == "REDIRECT"
    [task] = (await client.get("/v1/tasks")).json()
    assert task["status"] == "running" and task["redirects"] == 1
