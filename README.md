# Reliability

**The control plane for autonomous AI agents.** Agents perform tasks; Reliability
makes sure they perform them reliably — by observing every execution, detecting
failure patterns from real evidence, and *actually intervening* in a running
agent.

---

## The one idea that matters

Observing an agent does not give you the ability to stop it.

Most "agent observability" tools stream traces and stop there. Reliability's SDK
sits **inside the agent's own coroutine** and yields to the control plane at
checkpoints — entering a monitored run, before every tool call, before every
model call, and anywhere you ask for one explicitly. At a checkpoint the runtime
compares itself against the control plane's desired state and suspends, aborts,
or refuses a tool, and only *then* reports back that the command was applied.

The consequences are deliberate and visible throughout the product:

- An intervention is `pending` → `acknowledged` → `applied`. Nothing in the API
  or the UI claims success before the runtime confirms it.
- An agent that never reaches a checkpoint cannot be paused, and the dashboard
  keeps showing the command as pending rather than pretending otherwise.
- If the agent process dies, the execution is closed as `failed` with a reason
  that says exactly who concluded that and why.

---

## What's here

```
backend/    FastAPI + SQLAlchemy async. Auth, ingestion, detection, control plane.
sdk/        reliability-sdk — the Python package agents import.
dashboard/  Next.js 16 + TypeScript + Tailwind. Dark operator console.
demo/       Six demo agents and a control-plane acceptance script.
docker/     Postgres + Redis + backend + dashboard compose stack.
```

---

## Quick start (no Docker, no API keys, ~2 minutes)

Requires **Python 3.11+** and **Node 20+**.

```bash
# 1. Environment
cp .env.example .env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('DEV_SESSION_TOKEN=' + secrets.token_urlsafe(24))"
#    …paste those two values into .env

# 2. Python deps
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
.venv/Scripts/python -m pip install -e ./sdk
#    (macOS/Linux: .venv/bin/python)

# 3. Create the local user + an API key (printed once — copy into .env
#    as RELIABILITY_API_KEY)
cd backend && python -m app.seed && cd ..

# 4. Backend
cd backend && python -m uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
# 5. Dashboard
cd dashboard
npm install
echo "NEXT_PUBLIC_RELIABILITY_API_URL=http://127.0.0.1:8000" > .env.local
echo "NEXT_PUBLIC_RELIABILITY_TOKEN=<your DEV_SESSION_TOKEN>" >> .env.local
npm run dev
```

Open **http://localhost:3000**. In a third terminal, give it something to watch:

```bash
python demo/run_demo.py --list        # the six scenarios
python demo/run_demo.py healthy       # completes cleanly
python demo/run_demo.py loop          # trips loop detection
python demo/control_demo.py           # proves pause / resume / cancel end to end
```

Defaults are **SQLite** plus an **in-process event bus**, so nothing needs
installing. Point `DATABASE_URL` at Postgres and set `EVENT_BUS=redis` to switch;
the application code is driver-agnostic.

---

## Using the SDK in your own agent

```python
from reliability import Supervisor

supervisor = Supervisor(
    api_key="rl_live_…",         # or RELIABILITY_API_KEY
    agent_id="research-agent",   # stable name; registration is idempotent
)

@supervisor.monitor(task="Research AI startups")
async def research():
    async with supervisor.tool("search", {"q": "AI startups"}) as call:
        call.set_result(await my_search("AI startups"))

    async with supervisor.llm("gpt-4o-mini") as call:
        response = await my_model_call()
        call.record_usage(response.prompt_tokens, response.completion_tokens)

    return "done"

result = await research()
```

That is the whole integration. `tool()` and `llm()` are checkpoints; add
`await supervisor.checkpoint()` inside any long loop that goes through neither.

**Privacy defaults are conservative.** Tool arguments, tool results, prompts and
completions stay in your process unless you opt in with `CaptureConfig`. What is
always sent is a SHA-256 **fingerprint** of the arguments, computed locally —
which is what lets the backend detect identical repeated calls without ever
seeing the arguments themselves.

```python
from reliability import CaptureConfig

CaptureConfig()                  # default: metadata + error messages
CaptureConfig.metadata_only()    # strictest: names, counts, timings, fingerprints
CaptureConfig.full()             # everything except keys matching redact_keys
```

The SDK is **async-first**: `@supervisor.monitor` requires a coroutine function,
because cooperative suspension is what makes pause and cancel real. Synchronous
agents are not supported yet and the decorator says so rather than failing
subtly.

---

## Detection

Six deterministic detectors run on every ingested batch and on a 10-second
sweep. **None of them needs an LLM**, which is why loop and budget detection work
with no API key configured at all:

| Detector | Trips on |
|---|---|
| `repeated_tool_call` | N identical calls (same tool, same argument fingerprint) |
| `execution_timeout` | Running past the configured duration limit |
| `tool_call_limit` | More tool calls than the policy allows |
| `token_budget` | Reported token usage over the limit |
| `cost_budget` | Estimated cost over the limit |
| `repeated_errors` | N failures of the same target with the same error type |

Every incident carries **evidence assembled from stored rows** — event ids,
sequence numbers, timestamps — so a finding can always be traced back to the
trace that produced it.

An **LLM evaluator** sits behind a provider-agnostic interface (Gemini, OpenAI,
Anthropic, or any OpenAI-compatible gateway) and is strictly additive: it writes
a diagnosis onto an incident that deterministic rules already raised. It is never
required for an incident to exist, and it only runs when a human asks.

---

## Policies and authority

A policy sets the limits; the **supervision mode** decides what happens when one
trips:

| Mode | Behaviour |
|---|---|
| `observe` | Record and report. No recommendations, no interventions. |
| `assist` | Raise incidents with a recommended action. A human decides. |
| `autopilot` | Act automatically — but only actions listed in `permitted_interventions`, and never for a category in `require_approval_for`. |

Autopilot that wants to act outside its authorisation records *that it held
back*, and why. Limits themselves are always enforced deterministically in the
backend, never by a model.

Policies resolve agent-specific → workspace default → built-in defaults.
`GET /v1/policies/effective?agent_id=…` returns what will actually apply.

---

## Demo scenarios

```bash
python demo/run_demo.py healthy        # completes cleanly, no incident
python demo/run_demo.py loop           # identical tool call, forever
python demo/run_demo.py failing        # retries a dependency that is down
python demo/run_demo.py expensive      # blows the token budget
python demo/run_demo.py poor-output    # succeeds while answering the wrong question
python demo/run_demo.py long           # healthy but slow — pause/cancel it by hand
python demo/run_demo.py loop --autopilot   # let the supervisor pause it itself
```

`poor-output` is in the set on purpose: it is fast, cheap and error-free, so no
deterministic rule catches it. It marks where the deterministic layer stops and
the LLM output evaluator begins.

**`python demo/control_demo.py`** is the acceptance test for the whole premise.
It runs the looping agent, then drives the control plane over HTTP exactly as the
dashboard does, asserting at each step that the *runtime* confirmed the action:

```
[ok]  loop detected without any LLM involved
[ok]  incident evidence cites real repeated events  (3 calls to 'search', threshold 3)
[ok]  pause starts as pending, not reported as applied
[ok]  runtime confirmed the pause was applied
[ok]  agent stopped making tool calls while paused  (4 -> 4 over 3s)
[ok]  agent coroutine is suspended, not finished
[ok]  agent resumed making tool calls  (4 -> 5)
[ok]  agent code raised ExecutionCancelled at a checkpoint
[ok]  execution reports status=cancelled
```

---

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests sdk/tests -q     # 69 tests
cd dashboard && npx tsc --noEmit && npm run build
```

Coverage is aimed at the things that would be dangerous to get wrong: tenant
isolation, ingestion idempotency, each detector's threshold behaviour, the full
intervention lifecycle, autopilot's policy limits, stale-execution reaping, and
the SDK's cooperative pause/cancel/block enforcement.

---

## Architecture notes

**Control state** is derived by folding the ordered history of non-failed
commands, so a pause followed by a resume resolves correctly no matter how the
SDK polls. Cancel wins over everything.

**Delivery** is a long poll (`GET /v1/executions/{id}/control?wait=25`) that
parks on the event bus and returns the instant a command is issued — push latency
without a second protocol. Control state also rides along on every
ingest response, so a busy agent learns it must pause with no extra request.

**Liveness** has two signals: events, and control polls. The second keeps working
while an execution is paused and emitting nothing, which is how a paused agent
stays distinguishable from a dead one.

**Ingestion** is idempotent on `(execution_id, client_event_id)`, so a retried
batch after a network failure never double-counts.

**Costs** are arithmetic over reported token counts using a price table
(`backend/pricing.json` overrides it). An unknown model yields `None`, not a
guess, and the execution is flagged `cost_complete = false` so totals are
labelled partial instead of implying precision.

**User isolation**: every tenant-owned row carries `user_id` directly, so
isolation is one predicate on every query rather than a join that is easy to
forget. Cross-tenant access returns 404, never 403 — the existence of another
tenant's resource is not confirmed.

---

## Configuration

Everything is environment-driven; see `.env.example` for the annotated list.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | SQLite file | `postgresql+asyncpg://…` for Postgres |
| `EVENT_BUS` | `memory` | `redis` to fan out across processes |
| `SECRET_KEY` | — | generate one; never commit it |
| `DEV_SESSION_TOKEN` | — | the dashboard's local credential |
| `SUPERVISOR_LLM_PROVIDER` | `none` | `gemini` \| `openai` \| `anthropic` \| `fake` |
| `SUPERVISOR_LLM_API_KEY` | — | **set this in `.env`, never paste a key into chat** |
| `SUPERVISOR_MODEL` | `gemini-2.0-flash` | provider-specific model id |
| `STALE_EXECUTION_SECONDS` | `180` | silence after which an execution is reaped |

---

## Known limits

Stated plainly, because a reliability product that overstates itself is a
contradiction:

- **Schema changes are not migrated.** The backend uses `create_all`. Alembic
  belongs here before any deployment carrying data worth keeping; today a schema
  change means deleting `data/reliability.db`.
- **The Docker stack is unverified.** It was written without Docker available on
  the build machine. The SQLite + in-process route is the tested one.
- **One backend process** unless `EVENT_BUS=redis` — the in-memory bus only fans
  out within a single process.
- **Async Python only.** No sync agents, and no framework adapters yet; LangGraph
  and CrewAI integrations are deliberately not claimed.
- **Dashboard auth is a shared dev token**, not a login. Real user isolation is
  enforced server-side and already tested; only the session UI is deferred.
- **`request_approval` and `auto_retry`** exist in the policy schema but are not
  yet wired to runtime behaviour.
