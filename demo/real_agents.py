"""Real, model-driven demo agents.

Unlike ``agents.py`` - whose behaviour is scripted - every decision here is
made by a live Gemini model through function calling: which tool to call,
with what arguments, when to retry and when to stop. Token counts come from
the provider's ``usageMetadata``. Nothing about the trace is invented.

The scenarios shape the *environment* (a tool that is down, a job that never
finishes, a buggy prompt builder) and then let the model behave however it
behaves. Failure modes the supervisor catches are therefore emergent, which
is the point: these are the situations real agents get into.

Requires GEMINI_API_KEY. The model is DEMO_AGENT_MODEL (default
gemini-3.5-flash-lite).
"""
from __future__ import annotations

import ast
import asyncio
import json
import operator
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from reliability import CaptureConfig, ExecutionCancelled, Supervisor, ToolBlocked

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Wikipedia asks API clients to identify themselves. Generic on purpose.
WIKI_HEADERS = {"User-Agent": "ReliabilityDemoAgent/0.1 (local development demo)"}
WIKI_API = "https://en.wikipedia.org/w/api.php"
MAX_TOOL_RESULT_CHARS = 6000

ToolFn = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _wiki_get(params: dict[str, Any]) -> dict[str, Any]:
    """GET the MediaWiki API with the standard library.

    Deliberately not httpx: Wikimedia's bot filter currently answers httpx
    requests with 403 regardless of User-Agent, while urllib is accepted.
    """
    query = urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(f"{WIKI_API}?{query}", headers=WIKI_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


async def wikipedia_search(query: str) -> list[dict[str, str]]:
    """Real Wikipedia full-text search."""
    data = await asyncio.to_thread(
        _wiki_get, {"action": "query", "list": "search", "srsearch": query, "srlimit": 5}
    )
    hits = data.get("query", {}).get("search", [])
    return [
        {
            "title": hit["title"],
            "snippet": hit.get("snippet", "")
            .replace('<span class="searchmatch">', "")
            .replace("</span>", ""),
        }
        for hit in hits
    ]


async def wikipedia_page(title: str) -> dict[str, str]:
    """Plain-text extract of a Wikipedia article (truncated)."""
    data = await asyncio.to_thread(
        _wiki_get,
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "redirects": 1,
            "titles": title,
        },
    )
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if "missing" in page:
        raise LookupError(f"No Wikipedia article titled {title!r}")
    text = page.get("extract", "")
    return {"title": page.get("title", title), "text": text[:MAX_TOOL_RESULT_CHARS]}


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Only arithmetic expressions are supported")


async def calculator(expression: str) -> float:
    """Arithmetic only - parsed, never ``eval``-ed."""
    return _safe_eval(ast.parse(expression, mode="eval"))


class UpstreamUnavailable(RuntimeError):
    pass


async def wikipedia_search_down(query: str) -> list[dict[str, str]]:
    """Stands in for wikipedia_search during a (simulated) upstream outage."""
    await asyncio.sleep(0.3)
    raise UpstreamUnavailable("503 Service Unavailable from en.wikipedia.org")


async def wikipedia_page_down(title: str) -> dict[str, str]:
    await asyncio.sleep(0.3)
    raise UpstreamUnavailable("503 Service Unavailable from en.wikipedia.org")


async def get_report(report_id: str) -> dict[str, str]:
    """A report-generation job that never finishes - a classic polling trap."""
    await asyncio.sleep(0.5)
    return {
        "report_id": report_id,
        "status": "processing",
        "message": "The report is still being generated. Call get_report again to check.",
    }


DECLARATIONS: dict[str, dict[str, Any]] = {
    "wikipedia_search": {
        "name": "wikipedia_search",
        "description": "Search English Wikipedia. Returns article titles and snippets.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search terms"}},
            "required": ["query"],
        },
    },
    "wikipedia_page": {
        "name": "wikipedia_page",
        "description": "Fetch the plain text of a Wikipedia article by exact title.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Article title"}},
            "required": ["title"],
        },
    },
    "calculator": {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression, e.g. '828 - 330'.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    "get_report": {
        "name": "get_report",
        "description": "Fetch a generated financial report by id. Reports are generated "
        "asynchronously; status 'processing' means it is not ready yet.",
        "parameters": {
            "type": "object",
            "properties": {"report_id": {"type": "string"}},
            "required": ["report_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

class ModelError(RuntimeError):
    pass


@dataclass
class GeminiAgent:
    """A minimal function-calling agent, instrumented with the Reliability SDK.

    Every model call goes through ``supervisor.llm`` and every tool call
    through ``supervisor.tool``, so each one is a control checkpoint.
    """

    supervisor: Supervisor
    model: str
    system: str
    tools: dict[str, ToolFn]
    max_steps: int = 12
    api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))

    async def _generate(self, contents: list[dict[str, Any]]) -> dict[str, Any]:
        body = {
            "systemInstruction": {"parts": [{"text": self.system}]},
            "contents": contents,
            "tools": [{"functionDeclarations": [DECLARATIONS[n] for n in self.tools]}],
        }
        url = f"{GEMINI_BASE}/models/{self.model}:generateContent"
        delay = 2.0
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            for attempt in range(4):
                response = await client.post(
                    url, json=body, headers={"x-goog-api-key": self.api_key}
                )
                if response.status_code in (429, 500, 503) and attempt < 3:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if not response.is_success:
                    raise ModelError(f"{response.status_code}: {response.text[:400]}")
                return response.json()
        raise ModelError("unreachable")  # pragma: no cover

    async def run(self, task: str) -> str:
        if not self.api_key:
            raise ModelError("GEMINI_API_KEY is not set")
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": task}]}]

        for step in range(self.max_steps):
            async with self.supervisor.llm(self.model, prompt=contents[-1]) as call:
                data = await self._generate(contents)
                usage = data.get("usageMetadata", {})
                # Thinking tokens are billed as output, so they count here.
                call.record_usage(
                    prompt_tokens=usage.get("promptTokenCount", 0),
                    completion_tokens=usage.get("candidatesTokenCount", 0)
                    + usage.get("thoughtsTokenCount", 0),
                )
                candidates = data.get("candidates") or []
                if not candidates or "content" not in candidates[0]:
                    raise ModelError(f"model returned no content: {json.dumps(data)[:300]}")
                content = candidates[0]["content"]
                parts = content.get("parts", [])
                text = "".join(
                    p.get("text", "") for p in parts if "text" in p and not p.get("thought")
                )
                calls = [p["functionCall"] for p in parts if "functionCall" in p]
                call.set_completion(
                    text or "calls: " + ", ".join(c["name"] for c in calls)
                )

            # Keep the model's turn verbatim - Gemini needs its thought
            # signatures back to continue a function-calling exchange.
            contents.append(content)
            if not calls:
                return text.strip()

            responses = []
            for fc in calls:
                name, args = fc["name"], fc.get("args") or {}
                reply: dict[str, Any]
                try:
                    fn = self.tools.get(name)
                    if fn is None:
                        raise LookupError(f"Unknown tool {name!r}")
                    async with self.supervisor.tool(name, args) as tool_call:
                        result = tool_call.set_result(await fn(**args))
                    reply = {"result": result}
                except ExecutionCancelled:
                    raise
                except ToolBlocked as exc:
                    reply = {"error": f"Tool blocked by the operator: {exc}"}
                except Exception as exc:
                    # Hand the failure back to the model, as a real agent
                    # framework would, and let it decide what to do next.
                    reply = {"error": f"{type(exc).__name__}: {exc}"}
                part: dict[str, Any] = {"functionResponse": {"name": name, "response": reply}}
                if fc.get("id"):
                    part["functionResponse"]["id"] = fc["id"]
                responses.append(part)
            contents.append({"role": "user", "parts": responses})

        return f"Stopped after {self.max_steps} steps without a final answer."


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM = (
    "You are a careful research assistant. Use the tools to find facts; do not "
    "rely on memory for figures. Use the calculator for arithmetic. Finish with a "
    "concise answer that states the figures you used and where they came from."
)


def _truncating_prompt_builder(task: str) -> str:
    """A realistic bug: a template that clips the task to fit a 'short' field."""
    return task[:60]


@dataclass
class Scenario:
    agent: str
    description: str
    task: str
    system: str
    tools: dict[str, ToolFn]
    expect: str
    #: Incident categories the run is expected to raise (empty = none).
    expected_incidents: tuple[str, ...] = ()
    #: Transforms the task before the model sees it (the monitored task is
    #: always the original - that is what the operator asked for).
    prompt_builder: Callable[[str], str] = staticmethod(lambda t: t)  # type: ignore[assignment]
    max_steps: int = 12
    policy: dict[str, Any] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "research": Scenario(
        agent="gemini-research-agent",
        description="Answers a factual question with live Wikipedia lookups.",
        task="How much taller is the Burj Khalifa than the Eiffel Tower, in metres? "
        "State both heights you used.",
        system=RESEARCH_SYSTEM,
        tools={
            "wikipedia_search": wikipedia_search,
            "wikipedia_page": wikipedia_page,
            "calculator": calculator,
        },
        expect="Completes with a correct, sourced answer. No incident; output passes review.",
    ),
    "outage": Scenario(
        agent="gemini-outage-agent",
        description="Same research agent, but its knowledge source is down.",
        task="What year was the Golden Gate Bridge opened, and how long is its main span?",
        system=RESEARCH_SYSTEM
        + " Tools are occasionally flaky: if a call fails, retry it before giving up.",
        tools={
            "wikipedia_search": wikipedia_search_down,
            "wikipedia_page": wikipedia_page_down,
        },
        expect="Model retries the failing tool; repeated_errors incident is raised.",
        expected_incidents=("repeated_errors",),
    ),
    "polling": Scenario(
        agent="gemini-polling-agent",
        description="Waits on a report job that never finishes.",
        task="Get report Q3-2026-revenue and summarise total revenue and the "
        "largest cost line.",
        system="You are a finance assistant. Use get_report to fetch reports. "
        "Do not answer without the report's actual contents.",
        tools={"get_report": get_report},
        expect="Model polls with identical arguments; repeated_tool_call incident. "
        "With --autopilot the supervisor pauses the agent itself.",
        expected_incidents=("repeated_tool_call",),
        max_steps=15,
    ),
    "truncated": Scenario(
        agent="gemini-truncated-prompt-agent",
        description="A prompt-builder bug clips the task; the run looks healthy.",
        task="Compare the populations of Tokyo, Delhi and Shanghai and rank them, "
        "then name which of the three has the largest land area.",
        system=RESEARCH_SYSTEM,
        tools={
            "wikipedia_search": wikipedia_search,
            "wikipedia_page": wikipedia_page,
            "calculator": calculator,
        },
        prompt_builder=_truncating_prompt_builder,
        expect="Completes cleanly - no deterministic rule fires - but the output "
        "evaluator flags it as poor_output.",
        expected_incidents=("poor_output",),
    ),
}


def build(scenario: Scenario, model: str) -> tuple[Supervisor, Callable[[], Awaitable[str]]]:
    supervisor = Supervisor(
        agent_id=scenario.agent,
        framework="gemini-function-calling",
        description=scenario.description,
        capture=CaptureConfig(
            capture_tool_args=True,
            capture_tool_results=True,
            capture_output_summary=True,
            capture_completions=True,
            # Long enough that the output evaluator sees the whole answer.
            max_preview_chars=3000,
        ),
    )
    agent = GeminiAgent(
        supervisor=supervisor,
        model=model,
        system=scenario.system,
        tools=scenario.tools,
        max_steps=scenario.max_steps,
    )

    @supervisor.monitor(task=scenario.task)
    async def run() -> str:
        return await agent.run(scenario.prompt_builder(scenario.task))

    return supervisor, run
