# Reliability

**One supervisor for the AI agents you already run.** Reliability connects to
existing agent environments — Claude Code and OpenAI Agents SDK applications
today — observes what they do, detects loops, risky operations and work that
misses its objective, and intervenes where the platform allows it, all from
one dashboard.

You keep your agent platform. Reliability plugs into it through that
platform's own supported extension points (Claude Code lifecycle hooks; Agents
SDK run hooks, tool guardrails and tracing) and speaks one event protocol to a
central supervisor.

---

## How it fits together

```
 YOUR MACHINES / SERVERS                                   SUPERVISOR (cloud or local)
┌─────────────────────────────────────┐                  ┌──────────────────────────────────────┐
│ Claude Code ──hooks──► companion    │  universal       │ FastAPI backend                      │
│   (PreToolUse, Stop, …) hook script │  event protocol  │  • event processor + agent registry  │
│                                     │ ───────────────► │  • task tracker, session contexts    │
│ OpenAI Agents SDK app               │  /v1/events      │  • loop / progress / limit detectors │
│   └─ AgentsSupervisor adapter       │  /v1/decisions   │  • risk engine + policy engine       │
│      (run hooks, tool guardrails,   │ ◄─────────────── │  • decision + intervention engine    │
│       tracing processor)            │  allow / warn /  │  • output evaluator (LLM, tiered)    │
│                                     │  block / approve │  • approvals queue, audit trail      │
│ Custom Python agent ── SDK          │  / stop / pause  └───────────────┬──────────────────────┘
└─────────────────────────────────────┘                                  │ REST + WebSocket
                                                                ┌────────▼─────────┐
                                                                │ Web dashboard    │◄── operators
                                                                └──────────────────┘
```

* **Adapters are thin.** They translate platform events into the protocol and
  turn supervisor decisions back into platform actions. All reasoning lives in
  the backend; nothing about the supervisor is coupled to Claude or OpenAI.
* **Every integration declares its capabilities.** The backend never asks a
  platform for something it cannot do, and says so when a rule wanted more.
* **Observation never depends on control.** If the supervisor is unreachable,
  adapters fail open: the agent carries on and events are spooled.

## What each platform supports

| | Claude Code (hooks) | OpenAI Agents SDK | Custom Python SDK |
|---|---|---|---|
| Observe sessions, prompts, tool calls, sub-agents, completion | ✓ | ✓ | ✓ |
| Model calls and tokens | ✓ from the local transcript (best effort) | ✓ | ✓ |
| Block a tool call, with a reason the agent sees | ✓ `PreToolUse` deny | ✓ tool input guardrail | ✓ |
| Hold a call for human approval | ✓ (bounded by the hook timeout) | ✓ | — |
| Answer the platform's own permission prompts from the dashboard | ✓ `PermissionRequest` (opt-in) | — | — |
| Corrective feedback / send back at completion | ✓ `Stop` block | ✓ guardrail message | — |
| Stop | ✓ `continue: false` | ✓ at run-hook boundaries | ✓ |
| Pause / resume | **✗** — hooks cannot suspend a turn | ✓ at run-hook boundaries | ✓ |

Limitations are stated in the dashboard next to each integration and in
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

---

## Quick start (local)

Requires Python 3.11+ and Node 20+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pip install -e ./sdk -e ./companion

# .env at the repo root (see Configuration). At minimum:
#   SECRET_KEY=<random>   DEV_SESSION_TOKEN=<random>
#   SUPERVISOR_LLM_PROVIDER=gemini   GEMINI_API_KEY=<key>   (optional: output review)

cd backend && python -m app.seed && python -m uvicorn app.main:app --port 8000
```

```bash
cd dashboard && npm install
echo "NEXT_PUBLIC_RELIABILITY_API_URL=http://127.0.0.1:8000" > .env.local
npm run dev        # http://localhost:3000 → create an account
```

### Connect Claude Code

In the dashboard: **Integrations → Claude Code → Create integration key**, then:

```bash
python -m reliability_companion connect --api-url http://127.0.0.1:8000 --key rl_int_…
python -m reliability_companion claude-code install --project /path/to/project   # or --user
python -m reliability_companion doctor
```

`install` shows the exact change to `.claude/settings.json` and asks before
writing; it backs the file up and only ever touches its own entries.
`claude-code uninstall` removes them.

### Connect an OpenAI Agents SDK app

```bash
pip install -e ./sdk[openai-agents]
export RELIABILITY_API_URL=http://127.0.0.1:8000 RELIABILITY_INTEGRATION_KEY=rl_int_…
```

```python
from reliability.integrations.openai_agents import AgentsSupervisor

supervisor = AgentsSupervisor(app_name="support-bot")
result = await supervisor.run(triage_agent, user_input)        # instead of Runner.run

# or keep your own Runner call:
supervisor.instrument(triage_agent)                            # adds the tool guardrail
result = await Runner.run(triage_agent, user_input, hooks=supervisor.hooks())
```

---

## Supervision

**Modes** (per workspace or per agent): **observe** (record only),
**recommend** (the agent is told what is wrong, nothing is refused), **control**
(block, hold for approval, stop, pause — within the policy and the platform's
capabilities). Operator commands from the dashboard are not limited by mode.

**Decision points.** Before a tool runs (and, optionally, before an agent
finishes) the adapter asks the supervisor. Rules, first match wins:

1. operator stop / operator-blocked tool
2. policy deny list
3. risk rules — recursive deletes, disk writes, force-push / hard reset, DROP
   TABLE, `curl | sh`, credential files, publishing, unbounded shell loops, …
   (`GET /v1/risk-rules`; critical/high follow the policy's risk action, medium warns)
4. loop guard — the same call already returned the same result (or failed)
   N times in this task
5. approval list
6. completion review (opt-in) — the answer is reviewed against the task and
   the agent is sent back with the gaps, at most `max_redirects` times
7. allow

Every decision is stored with its rule and reason; when the mode or the
platform forced a milder action, the decision records what was intended and why.

**Detectors** run on every batch and a 10-second sweep, scoped to the current
task: identical tool calls with identical results, repeated failures, tool-call
budget, duration, stalls (no activity with nothing in flight), and session-wide
token and cost budgets. No LLM needed.

**Output evaluator** (optional, Gemini/OpenAI/Anthropic): a fast model screens
each finished task; only a confident failure is confirmed by the deeper model.
It checks grounding — claims must be backed by successful tool results in the
trace. Identical questions are answered from cache; every call's tokens, cost
and latency appear under **Reliability → What supervision costs**.

**Reliability score** = `100 × (0.5 × task success + 0.3 × incident-free rate +
0.2 × tool success)`, shown only after 3 finished tasks.

## Universal event protocol

`POST /v1/events` (batched) and `POST /v1/decisions` (one pending event →
one action). Fields: `event_id, platform, integration_id, agent_id,
session_id, event_type, timestamp, task_id, execution_context, payload`.
Types: `AGENT_REGISTERED, SESSION_STARTED, SESSION_ENDED, TASK_CREATED,
TASK_UPDATED, TASK_COMPLETED, PROMPT_SUBMITTED, MODEL_CALL, TOOL_CALL_STARTED,
TOOL_CALL_COMPLETED, TOOL_CALL_FAILED, SUBAGENT_STARTED, SUBAGENT_COMPLETED,
AGENT_HANDOFF, AGENT_ERROR, AGENT_IDLE, AGENT_STOPPED` plus control extensions
(`TOOL_CALL_BLOCKED, SESSION_PAUSED/RESUMED, GUARDRAIL_RESULT,
SUPERVISOR_DECISION, APPROVAL_REQUESTED/RESOLVED, NOTIFICATION, LOG`).
Idempotent on `event_id`; agents, sessions and tasks are created on first
sight, so stateless adapters need no setup call. See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md#protocol) for writing a new adapter.

## Dashboard

Overview (per platform) · Connected agents · Live activity · Sessions with
full traces · Tasks · Approvals queue · Detected issues · Interventions
(decision log) · Reliability scores and evaluation cost · Ask the supervisor ·
Integrations (keys, setup, capabilities, limits) · Policies · Settings.
Real accounts: each sign-up is an isolated workspace.

## Credentials

* **User API key** `rl_live_…` — full workspace access (scripts, custom SDK).
* **Integration key** `rl_int_…` — scoped to one integration: send events,
  request decisions, wait on approvals, poll control. Cannot read the dashboard
  or change policy. This is what gets installed on a developer machine.
* **Session token** `rs_…` — dashboard sign-in; signed, expiring.
* **Dev token** — local convenience, refused unless `RELIABILITY_ENV=development`.

---

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests sdk/tests companion/tests -q   # 131 tests
cd dashboard && npx tsc --noEmit && npm run build
```

**Live integration tests** drive real agents — not synthetic events — and
check what the supervisor recorded:

```bash
# Real headless Claude Code sessions (uses your Claude plan):
python integrations_tests/claude_code_live.py --project <project with hooks>     # benign, approval, loop, stop
# Real Agents SDK runs (model via Gemini's OpenAI-compatible endpoint):
python integrations_tests/openai_agents_live.py --app helpdesk                   # handoffs, approve, deny, pause, stop, guardrail, tool failure, concurrency
python integrations_tests/openai_agents_live.py --app examples --examples <openai-agents-python>/examples
# Both platforms at once:
python integrations_tests/mixed_concurrency_live.py --project <project with hooks>
```

## Deployment

No database service is needed: the supervisor keeps everything in one SQLite
file. The free setup is the dashboard on Vercel (root `dashboard/`) and the
supervisor on your own machine — reachable from the site directly on
`localhost` or through a free tunnel; the sign-in page lets you point the
dashboard at any supervisor address without rebuilding. Step-by-step in
[docs/DEPLOY.md](docs/DEPLOY.md). For always-on hosting, `backend/Dockerfile`,
`railway.json` and `fly.toml` are included, and hosted Postgres URLs work
as given.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `RELIABILITY_ENV` | `development` | set `production` when deployed (enforces a real `SECRET_KEY`, disables the dev token) |
| `SECRET_KEY` | — | signs dashboard sessions; long random value |
| `DATABASE_URL` | SQLite file | Postgres for deployments |
| `EVENT_BUS` | `memory` | `redis` to run more than one backend process |
| `CORS_ORIGINS` | localhost:3000 | the dashboard's origin when deployed |
| `ALLOW_SIGNUP` | `true` | turn off once your accounts exist |
| `SUPERVISOR_LLM_PROVIDER` | `none` | `gemini` \| `openai` \| `anthropic` |
| `SUPERVISOR_MODEL` / `SUPERVISOR_FAST_MODEL` | `gemini-3.5-flash` / same | deep and fast evaluator tiers |
| `SUPERVISOR_LLM_API_KEY` | — | falls back to `GEMINI_API_KEY` for gemini |
| `STALE_EXECUTION_SECONDS` | 180 | heartbeat platforms: silence ⇒ failed |
| `INACTIVE_SESSION_SECONDS` | 1800 | hook platforms: silence ⇒ inactive (never failed) |
| `KEEPALIVE_URL` / `KEEPALIVE_INTERVAL_SECONDS` | off / 600 | optional self-ping, for hosts that sleep when idle |

## Known limits

* **Schema migrations**: `create_all` only. Introduce Alembic before keeping
  data across schema changes in production.
* **Claude Code** cannot be paused; usage comes from its local transcript,
  whose format is internal to Claude Code (best effort, never required).
* **Agents SDK**: hosted tools are observed, not intercepted; control applies
  at hook boundaries, not inside an in-flight model call.
* **Risk rules** are pattern rules: they catch recognisable dangerous
  commands and never claim an unmatched one is safe.
* **Desktop companion** is a CLI today; the GUI, cross-agent analysis, MCP and
  further frameworks are phase 2–3 ([docs/REWORK_PLAN.md](docs/REWORK_PLAN.md)).
* **One backend process** unless `EVENT_BUS=redis`.
