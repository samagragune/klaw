"""API behaviour: auth isolation, ingestion, detection, control lifecycle."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


def event(seq: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "client_event_id": uuid.uuid4().hex,
        "seq": seq,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload or {},
    }


async def register(client, name="agent-a") -> str:
    response = await client.post(
        "/v1/agents/register", json={"name": name, "framework": "custom"}
    )
    assert response.status_code == 200
    return response.json()["id"]


async def start(client, agent_id: str, task="do the thing") -> str:
    response = await client.post(
        "/v1/executions", json={"agent_id": agent_id, "task": task}
    )
    assert response.status_code == 201
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Authentication and isolation
# ---------------------------------------------------------------------------

async def test_requests_without_a_key_are_rejected(client):
    response = await client.get("/v1/agents", headers={"Authorization": ""})
    assert response.status_code == 401


async def test_invalid_key_is_rejected(client):
    response = await client.get(
        "/v1/agents", headers={"Authorization": "Bearer rl_live_nope"}
    )
    assert response.status_code == 401


async def test_dashboard_session_token_resolves_to_the_dev_user(client):
    response = await client.get(
        "/v1/config", headers={"Authorization": "Bearer test-dashboard-token"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "test@reliability.local"


async def test_one_tenant_cannot_see_or_touch_another(client, other_client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)

    assert (await other_client.get("/v1/agents")).json() == []
    assert (await other_client.get(f"/v1/executions/{execution_id}")).status_code == 404
    # Control is refused the same way: 404, never a hint that the row exists.
    response = await other_client.post(
        f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
    )
    assert response.status_code == 404


async def test_registration_is_idempotent(client):
    first = await register(client, "same-name")
    second = await register(client, "same-name")
    assert first == second
    assert len((await client.get("/v1/agents")).json()) == 1


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

async def test_ingest_updates_rollups_and_is_idempotent(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)

    batch = [
        event(0, "execution.started"),
        event(1, "tool.started", {"tool_name": "search", "args_fingerprint": "abc"}),
        event(2, "tool.completed", {"tool_name": "search"}),
        event(
            3,
            "llm.completed",
            {"model": "demo-model", "prompt_tokens": 1000, "completion_tokens": 200},
        ),
    ]
    first = await client.post(
        "/v1/ingest/events", json={"execution_id": execution_id, "events": batch}
    )
    assert first.json()["accepted"] == 4

    replay = await client.post(
        "/v1/ingest/events", json={"execution_id": execution_id, "events": batch}
    )
    assert replay.json() == {**replay.json(), "accepted": 0, "duplicates": 4}

    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["tool_call_count"] == 1
    assert detail["token_usage"] == 1200
    # demo-model is priced, so the cost is arithmetic rather than a guess.
    assert detail["estimated_cost"] == pytest.approx((1000 * 0.5 + 200 * 1.5) / 1e6)
    assert detail["cost_complete"] is True


async def test_unknown_model_leaves_cost_incomplete_rather_than_guessed(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(
                    0,
                    "llm.completed",
                    {"model": "some-unlisted-model", "prompt_tokens": 50, "completion_tokens": 5},
                )
            ],
        },
    )
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["token_usage"] == 55
    assert detail["estimated_cost"] is None
    assert detail["cost_complete"] is False


async def test_unknown_event_type_is_rejected(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    response = await client.post(
        "/v1/ingest/events",
        json={"execution_id": execution_id, "events": [event(0, "tool.exploded")]},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

async def test_repeated_tool_calls_raise_one_incident_with_evidence(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)

    events = [
        event(i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
        for i in range(4)
    ]
    await client.post(
        "/v1/ingest/events", json={"execution_id": execution_id, "events": events}
    )

    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    loop = [i for i in incidents if i["category"] == "repeated_tool_call"]
    assert len(loop) == 1
    evidence = loop[0]["evidence"]
    assert evidence["occurrences"] == 4
    assert evidence["tool_name"] == "search"
    assert len(evidence["events"]) == 4
    # Ingesting more of the same must not multiply incidents.
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(10 + i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
                for i in range(3)
            ],
        },
    )
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    assert len([i for i in incidents if i["category"] == "repeated_tool_call"]) == 1


async def test_distinct_arguments_are_not_a_loop(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(i, "tool.started", {"tool_name": "search", "args_fingerprint": f"fp{i}"})
                for i in range(6)
            ],
        },
    )
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    assert [i for i in incidents if i["category"] == "repeated_tool_call"] == []


async def test_repeated_errors_raise_an_incident(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(
                    i,
                    "tool.failed",
                    {"tool_name": "api", "error_type": "UpstreamUnavailable", "error": "503"},
                )
                for i in range(3)
            ],
        },
    )
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    assert any(i["category"] == "repeated_errors" for i in incidents)


async def test_token_budget_incident_respects_policy(client):
    agent_id = await register(client)
    await client.put(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "supervision_mode": "assist",
            "configuration": {"max_tokens": 100, "repeated_tool_call_threshold": 3},
        },
    )
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(
                    0,
                    "llm.completed",
                    {"model": "demo-model", "prompt_tokens": 500, "completion_tokens": 0},
                )
            ],
        },
    )
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    budget = next(i for i in incidents if i["category"] == "token_budget")
    assert budget["evidence"]["limit"] == 100
    assert budget["evidence"]["token_usage"] == 500


# ---------------------------------------------------------------------------
# Control lifecycle
# ---------------------------------------------------------------------------

async def test_pause_is_not_applied_until_the_runtime_acknowledges(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)

    created = (
        await client.post(
            f"/v1/executions/{execution_id}/interventions",
            json={"action": "pause", "reason": "test"},
        )
    ).json()
    assert created["status"] == "pending"
    assert created["applied_at"] is None

    state = (await client.get(f"/v1/executions/{execution_id}/control")).json()
    assert state["desired_state"] == "paused"
    assert [c["intervention_id"] for c in state["commands"]] == [created["id"]]

    acked = (
        await client.post(
            "/v1/interventions/ack",
            json={"intervention_id": created["id"], "status": "acknowledged"},
        )
    ).json()
    assert acked["status"] == "acknowledged"
    assert acked["applied_at"] is None

    applied = (
        await client.post(
            "/v1/interventions/ack",
            json={
                "intervention_id": created["id"],
                "status": "applied",
                "detail": {"checkpoint": "tool:search"},
            },
        )
    ).json()
    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None
    assert applied["runtime_result"]["checkpoint"] == "tool:search"


async def test_resume_clears_a_pause(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)

    pause = (
        await client.post(
            f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
        )
    ).json()
    await client.post(
        "/v1/interventions/ack", json={"intervention_id": pause["id"], "status": "applied"}
    )
    assert (await client.get(f"/v1/executions/{execution_id}/control")).json()[
        "desired_state"
    ] == "paused"

    resume = (
        await client.post(
            f"/v1/executions/{execution_id}/interventions", json={"action": "resume"}
        )
    ).json()
    await client.post(
        "/v1/interventions/ack", json={"intervention_id": resume["id"], "status": "applied"}
    )
    state = (await client.get(f"/v1/executions/{execution_id}/control")).json()
    assert state["desired_state"] == "running"
    assert state["commands"] == []


async def test_cancel_wins_over_a_pause(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
    )
    await client.post(
        f"/v1/executions/{execution_id}/interventions", json={"action": "cancel"}
    )
    state = (await client.get(f"/v1/executions/{execution_id}/control")).json()
    assert state["desired_state"] == "cancelled"


async def test_blocked_tool_appears_in_control_state(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    response = await client.post(
        f"/v1/executions/{execution_id}/interventions",
        json={"action": "block_tool", "parameters": {"tool_name": "delete_everything"}},
    )
    assert response.status_code == 201
    state = (await client.get(f"/v1/executions/{execution_id}/control")).json()
    assert state["blocked_tools"] == ["delete_everything"]


async def test_block_tool_requires_a_tool_name(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    response = await client.post(
        f"/v1/executions/{execution_id}/interventions", json={"action": "block_tool"}
    )
    assert response.status_code == 422


async def test_finished_executions_cannot_be_controlled(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={"execution_id": execution_id, "events": [event(0, "execution.completed")]},
    )
    response = await client.post(
        f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
    )
    assert response.status_code == 409


async def test_pending_commands_expire_when_the_execution_ends(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    pause = (
        await client.post(
            f"/v1/executions/{execution_id}/interventions", json={"action": "pause"}
        )
    ).json()
    await client.post(
        "/v1/ingest/events",
        json={"execution_id": execution_id, "events": [event(0, "execution.completed")]},
    )
    rows = (await client.get(f"/v1/executions/{execution_id}/interventions")).json()
    expired = next(r for r in rows if r["id"] == pause["id"])
    assert expired["status"] == "expired"
    assert expired["applied_at"] is None


async def test_autopilot_pauses_within_policy_and_records_why(client):
    agent_id = await register(client)
    await client.put(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "supervision_mode": "autopilot",
            "configuration": {
                "repeated_tool_call_threshold": 3,
                "permitted_interventions": ["pause"],
            },
        },
    )
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
                for i in range(3)
            ],
        },
    )
    rows = (await client.get(f"/v1/executions/{execution_id}/interventions")).json()
    assert len(rows) == 1
    assert rows[0]["action"] == "pause"
    assert rows[0]["requested_by"] == "supervisor"
    # Requested, not yet applied: no runtime has confirmed anything.
    assert rows[0]["status"] == "pending"
    assert rows[0]["applied_at"] is None


async def test_autopilot_will_not_take_an_action_outside_the_policy(client):
    agent_id = await register(client)
    await client.put(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "supervision_mode": "autopilot",
            "configuration": {
                "repeated_tool_call_threshold": 3,
                # Pause deliberately withheld.
                "permitted_interventions": ["cancel"],
            },
        },
    )
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
                for i in range(3)
            ],
        },
    )
    assert (await client.get(f"/v1/executions/{execution_id}/interventions")).json() == []
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    assert "not in the policy" in incidents[0]["action_taken"]


async def test_observe_mode_reports_without_recommending_action(client):
    agent_id = await register(client)
    await client.put(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "supervision_mode": "observe",
            "configuration": {"repeated_tool_call_threshold": 3},
        },
    )
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
                for i in range(3)
            ],
        },
    )
    incidents = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()
    assert incidents[0]["recommended_action"] is None
    assert (await client.get(f"/v1/executions/{execution_id}/interventions")).json() == []


# ---------------------------------------------------------------------------
# Policies, metrics, keys, evaluator
# ---------------------------------------------------------------------------

async def test_effective_policy_prefers_agent_over_user_default(client):
    agent_id = await register(client)
    await client.put(
        "/v1/policies",
        json={"supervision_mode": "observe", "configuration": {"max_tool_calls": 10}},
    )
    effective = (
        await client.get("/v1/policies/effective", params={"agent_id": agent_id})
    ).json()
    assert effective["source"] == "user_default"
    assert effective["supervision_mode"] == "observe"

    await client.put(
        "/v1/policies",
        json={
            "agent_id": agent_id,
            "supervision_mode": "autopilot",
            "configuration": {"max_tool_calls": 5},
        },
    )
    effective = (
        await client.get("/v1/policies/effective", params={"agent_id": agent_id})
    ).json()
    assert effective["source"] == "agent"
    assert effective["configuration"]["max_tool_calls"] == 5


async def test_overview_counts_only_the_callers_data(client, other_client):
    agent_id = await register(client)
    await start(client, agent_id)
    other_agent = await register(other_client, "their-agent")
    await start(other_client, other_agent)

    mine = (await client.get("/v1/metrics/overview")).json()
    assert mine["total_agents"] == 1
    assert mine["executions_total"] == 1


async def test_api_key_is_returned_once_and_stored_hashed(client):
    created = (await client.post("/v1/keys", params={"name": "ci"})).json()
    assert created["key"].startswith("rl_live_")
    listed = (await client.get("/v1/keys")).json()
    assert all("key" not in row for row in listed)
    assert any(row["prefix"] == created["prefix"] for row in listed)


async def test_revoked_key_stops_working(client):
    created = (await client.post("/v1/keys", params={"name": "temp"})).json()
    headers = {"Authorization": f"Bearer {created['key']}"}
    assert (await client.get("/v1/agents", headers=headers)).status_code == 200
    await client.delete(f"/v1/keys/{created['id']}")
    assert (await client.get("/v1/agents", headers=headers)).status_code == 401


async def test_diagnosis_is_refused_rather_than_faked_without_a_provider(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(i, "tool.started", {"tool_name": "search", "args_fingerprint": "same"})
                for i in range(3)
            ],
        },
    )
    incident = (
        await client.get("/v1/incidents", params={"execution_id": execution_id})
    ).json()[0]
    response = await client.post(f"/v1/incidents/{incident['id']}/diagnose")
    assert response.status_code == 409
    assert "SUPERVISOR_LLM_PROVIDER" in response.json()["detail"]


async def test_supervisor_chat_says_so_instead_of_inventing_an_answer(client):
    response = await client.post(
        "/v1/supervisor/chat", json={"message": "why did my agent fail?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["evaluator"] is None
    assert body["unavailable_reason"]


async def test_control_actions_are_audited(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        f"/v1/executions/{execution_id}/interventions",
        json={"action": "pause", "reason": "because"},
    )
    actions = [row["action"] for row in (await client.get("/v1/audit")).json()]
    assert "intervention.pause" in actions
    assert "agent.registered" in actions


# ---------------------------------------------------------------------------
# Timestamp correctness
# ---------------------------------------------------------------------------

async def test_every_timestamp_the_api_returns_carries_a_timezone(client):
    """A bare timestamp is read by browsers as local time, not UTC.

    SQLite has no timezone type, so without normalisation every trace event
    would render hours out in the dashboard.
    """
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(0, "tool.started", {"tool_name": "s", "args_fingerprint": "f"}),
                event(1, "execution.completed", {}),
            ],
        },
    )

    def assert_aware(value: str | None, label: str) -> None:
        if value is None:
            return
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, f"{label} has no timezone: {value!r}"

    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    for field in ("started_at", "completed_at", "last_event_at", "last_heartbeat_at"):
        assert_aware(detail[field], f"execution.{field}")
    for stored in detail["events"]:
        assert_aware(stored["timestamp"], "event.timestamp")

    agents = (await client.get("/v1/agents")).json()
    assert_aware(agents[0]["created_at"], "agent.created_at")
    assert_aware(agents[0]["last_seen_at"], "agent.last_seen_at")


async def test_event_timestamps_survive_a_round_trip_unchanged(client):
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    sent = datetime(2026, 3, 1, 12, 34, 56, tzinfo=timezone.utc)
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                {
                    "client_event_id": uuid.uuid4().hex,
                    "seq": 0,
                    "event_type": "log",
                    "timestamp": sent.isoformat(),
                    "payload": {"message": "hello"},
                }
            ],
        },
    )
    stored = (await client.get(f"/v1/executions/{execution_id}/events")).json()[0]
    assert datetime.fromisoformat(stored["timestamp"]) == sent
