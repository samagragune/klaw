"""Model-based output review, provider resilience and key configuration.

No network: the provider is a stub returning a fixed verdict, and the retry
test runs against an httpx mock transport.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.supervision import output_review, providers
from app.supervision.providers import LLMProvider

from tests.test_api import event, register, start

pytestmark = pytest.mark.asyncio


class StubProvider(LLMProvider):
    name = "stub"

    def __init__(self, verdict: dict) -> None:
        super().__init__(model="stub-model", api_key="")
        self.verdict = verdict
        self.prompts: list[str] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.verdict)


FAILING = {
    "satisfies_objective": False,
    "grounded": False,
    "gaps": ["The ranking was never produced"],
    "reasoning": "Only two of the three cities are discussed.",
    "confidence": "high",
}


@pytest.fixture
def evaluator(monkeypatch):
    """Configure a stub evaluator everywhere the review path looks for one."""

    def install(verdict: dict) -> StubProvider:
        stub = StubProvider(verdict)
        monkeypatch.setattr(output_review, "get_provider", lambda tier="deep": stub)
        monkeypatch.setattr("app.routers.executions.provider_configured", lambda: True)
        monkeypatch.setattr("app.routers.control.provider_configured", lambda: True)
        return stub

    return install


async def finished_run(client, *, opt_in: bool | None = None, output="Tokyo is big.") -> str:
    agent_id = await register(client)
    if opt_in is not None:
        await client.put(
            "/v1/policies",
            json={
                "agent_id": agent_id,
                "configuration": {"evaluate_output_on_completion": opt_in},
            },
        )
    execution_id = await start(client, agent_id, task="Rank Tokyo, Delhi and Shanghai")
    await client.post(
        "/v1/ingest/events",
        json={
            "execution_id": execution_id,
            "events": [
                event(0, "execution.started"),
                event(1, "execution.completed", {"output_summary": output}),
            ],
        },
    )
    return execution_id


async def drain_background_reviews() -> None:
    if output_review._background:
        await asyncio.gather(*list(output_review._background))


async def test_confident_failing_verdict_raises_an_llm_labelled_incident(client, evaluator):
    stub = evaluator(FAILING)
    execution_id = await finished_run(client)

    response = await client.post(f"/v1/executions/{execution_id}/evaluate-output")
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"]["satisfies_objective"] is False
    assert body["incident_id"]

    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    [incident] = detail["incidents"]
    assert incident["category"] == "poor_output"
    # Never mistaken for a deterministic finding.
    assert incident["source"] == "llm"
    assert incident["evidence"]["evaluator"] == "stub:stub-model"
    assert incident["evidence"]["gaps"] == FAILING["gaps"]
    assert detail["output_evaluation"]["confidence"] == "high"
    # The model saw the stored task and output, not something invented.
    assert "Rank Tokyo, Delhi and Shanghai" in stub.prompts[0]
    assert "Tokyo is big." in stub.prompts[0]


async def test_re_evaluation_updates_the_same_incident(client, evaluator):
    evaluator(FAILING)
    execution_id = await finished_run(client)
    first = (await client.post(f"/v1/executions/{execution_id}/evaluate-output")).json()
    second = (await client.post(f"/v1/executions/{execution_id}/evaluate-output")).json()
    assert first["incident_id"] == second["incident_id"]


async def test_low_confidence_failure_is_recorded_but_raises_nothing(client, evaluator):
    evaluator({**FAILING, "confidence": "low"})
    execution_id = await finished_run(client)
    body = (await client.post(f"/v1/executions/{execution_id}/evaluate-output")).json()
    assert body["incident_id"] is None
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["incidents"] == []
    assert detail["output_evaluation"]["satisfies_objective"] is False


async def test_a_running_execution_cannot_be_evaluated(client, evaluator):
    evaluator(FAILING)
    agent_id = await register(client)
    execution_id = await start(client, agent_id)
    response = await client.post(f"/v1/executions/{execution_id}/evaluate-output")
    assert response.status_code == 409


async def test_evaluation_is_refused_without_a_provider(client):
    execution_id = await finished_run(client)
    response = await client.post(f"/v1/executions/{execution_id}/evaluate-output")
    assert response.status_code == 409


async def test_policy_opt_in_reviews_every_completed_run(client, evaluator):
    evaluator(FAILING)
    execution_id = await finished_run(client, opt_in=True)
    await drain_background_reviews()
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert [i["category"] for i in detail["incidents"]] == ["poor_output"]


async def test_without_opt_in_no_output_leaves_for_the_provider(client, evaluator):
    stub = evaluator(FAILING)
    execution_id = await finished_run(client, opt_in=False)
    await drain_background_reviews()
    assert stub.prompts == []
    detail = (await client.get(f"/v1/executions/{execution_id}")).json()
    assert detail["output_evaluation"] is None


async def test_provider_retries_transient_overload(monkeypatch):
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        return httpx.Response(code, json={"ok": code == 200})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        response = await providers._post(http, "https://model.test/generate", json={})
    assert response.status_code == 200


async def test_provider_error_keeps_the_explanation_from_the_body():
    request = httpx.Request("POST", "https://model.test/generate")
    response = httpx.Response(404, text="model retired, use model-v2", request=request)
    with pytest.raises(httpx.HTTPStatusError, match="use model-v2"):
        providers._raise_for_status(response)


async def test_gemini_key_is_accepted_as_the_supervisor_key():
    settings = Settings(
        _env_file=None,
        supervisor_llm_provider="gemini",
        supervisor_llm_api_key="",
        gemini_api_key="from-gemini-var",
    )
    assert settings.supervisor_llm_api_key == "from-gemini-var"


async def test_an_explicit_supervisor_key_wins():
    settings = Settings(
        _env_file=None,
        supervisor_llm_provider="gemini",
        supervisor_llm_api_key="explicit",
        gemini_api_key="from-gemini-var",
    )
    assert settings.supervisor_llm_api_key == "explicit"
