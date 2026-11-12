# Rework plan — from agent SDK to multi-platform supervisor

Status: **phase 1 implemented** (deployment awaiting hosting accounts). Report at the end. This file is the implementation plan required before
substantial changes; the final implementation report will be appended to it.

## 1. What exists and what is reused

| Existing component | Fate |
|---|---|
| FastAPI backend, API-key auth, per-tenant isolation (404 on cross-tenant) | **Kept** |
| Idempotent ingestion, incremental rollups, live fan-out (bus + WebSocket) | **Kept**, fed by the new protocol |
| Deterministic detectors (loop, repeated errors, tokens, cost, tool count, timeout) | **Kept**, re-keyed to protocol event types |
| Policy engine, observe/assist/autopilot, permitted interventions | **Kept**, extended with capability awareness and risk rules |
| Intervention lifecycle (pending → acknowledged → applied, never claimed early) | **Kept** for platforms with runtime control |
| LLM evaluator: diagnosis, output review (grounding), supervisor chat | **Kept**, behind the same provider interface |
| Sweeper / stale-session reaping, audit log, pricing | **Kept** |
| Python SDK (`Supervisor`) | **Kept** as the *Custom Agent SDK* adapter |
| Next.js dashboard | **Kept and restructured** around platforms, sessions, tasks, approvals |
| Shared dev token baked into the dashboard bundle | **Replaced** — it is readable by anyone who loads the page; a public site needs real login |

## 2. Model mapping

The plan's vocabulary maps onto the existing schema with additions, not a rebuild:

- **Integration** (new table): one connected environment — platform, name,
  declared capabilities, scoped key, status, last seen.
- **Agent** (existing): now carries `platform`, `integration_id` and an
  `external_id` (the platform's own identity). Auto-registered on first event.
- **Session** = existing `Execution` row, plus `platform`, `external_session_id`
  and `parent_execution_id` (sub-agents). Auto-created on first event.
- **Task** (new table): objective, status, timeline, output — e.g. one Claude
  Code prompt-to-stop cycle, or one Agents SDK run.
- **ApprovalRequest** (new table): human approval queue, decided on the dashboard.
- Events keep the existing table; their `event_type` becomes the protocol type.

Schema changes are applied with `create_all` on a fresh database. The local dev
database holds only demo data and is reset; Alembic is required before any
deployment that must keep data across schema changes (tracked as remaining work).

## 3. Universal event protocol

`POST /v1/events` — a batch of events with the fields the brief requires
(`event_id, platform, integration_id, agent_id, session_id, event_type,
timestamp, task_id, execution_context, payload`). Idempotent on `event_id`;
ordered by a per-session sequence assigned at ingest, with the event timestamp
preserved; agent, session and task resolved or created on arrival, so stateless
adapters (one process per Claude Code hook) work.

`POST /v1/decisions` — the synchronous decision point. An adapter at a
blocking boundary (Claude Code `PreToolUse`, Agents SDK tool guardrail) sends
the pending event and receives `ALLOW | WARN | BLOCK | REQUEST_APPROVAL | STOP`
with a reason and optional corrective feedback. Decisions are filtered by the
integration's declared capabilities and written to the audit trail.

The legacy `/v1/ingest/events` endpoint is kept as a mapping onto the same
pipeline so the existing SDK keeps working.

## 4. Capabilities (declared per integration, enforced by the backend)

| Capability | Claude Code (hooks) | OpenAI Agents SDK | Custom SDK |
|---|---|---|---|
| observe | yes | yes | yes |
| block tool | yes (PreToolUse deny) | yes (tool input guardrail) | yes |
| request approval | yes (hook waits, bounded by its timeout) | yes (`needs_approval` interruptions) | no |
| corrective feedback | yes (deny reason / Stop block reason) | yes (guardrail message) | no |
| stop | yes (`continue: false`) | yes (raised at a run-hook boundary) | yes |
| pause / resume | **no** — hooks cannot suspend a turn | yes (awaited run hooks) | yes |

The backend never issues an action the integration did not declare.

## 5. Phase 1 build order (each step tested before the next)

1. **Core refactor + protocol**: models, `/v1/events`, `/v1/decisions`,
   integrations/registry, tasks, capability-aware decisions, risk rules,
   approval queue. Unit and API tests.
2. **Claude Code adapter**: stdlib-only hook forwarder (fails open, spools
   offline), plus a setup CLI that installs hooks into a project's or the
   user's settings only after showing the change and getting consent.
   Tested with real headless `claude -p` sessions, not synthetic events.
3. **OpenAI Agents SDK adapter**: tracing processor (observation, handoffs,
   guardrails, usage), run hooks (stop/pause at boundaries), tool guardrail
   (block with feedback), approval loop. Tested with real Agents SDK runs on
   Gemini through its OpenAI-compatible endpoint.
4. **Concurrency test**: several Claude Code sessions and Agents SDK runs at once.
5. **Dashboard**: overview by platform, connected agents, live activity,
   sessions/traces, tasks, reliability scores, issues, interventions, approval
   queue, integrations (keys + setup), policies, evaluation cost; real login.
6. **Deployment** of backend + dashboard to a public URL.
7. Docs, README, final implementation report.

## 6. Explicitly not in phase 1

Desktop GUI companion (phase 2 — the setup CLI covers hook installation now),
cross-agent analysis, MCP, browser extension, other frameworks and providers.

## 7. Constraints noted

- Memory on the build machine is tight (~2.5 GB free); processes are run a few
  at a time.
- Real Claude Code sessions used for testing consume the account's Claude usage;
  they are kept short.


---

# Implementation report (phase 1)

## Completed

| Area | What exists now |
|---|---|
| Universal protocol | `POST /v1/events`, `POST /v1/decisions`; canonical event vocabulary; legacy SDK events translated at ingestion; idempotent; agents/sessions/tasks resolved on first sight |
| Agent registry & sessions | `Integration` (scoped keys, declared capabilities, connection state), platform identity on agents and sessions, sub-session parent links, session context |
| Task tracker | `Task` per objective (prompt→stop, SDK run); abandoned vs completed vs cancelled told apart; per-task rollups |
| Detectors | task-scoped loop (identical args **and** identical result), repeated failures, tool budget, duration, stall; session token/cost budgets |
| Risk engine | 18 pattern rules incl. recursive delete, disk write, force-push, hard reset, DROP, pipe-to-shell, credentials, publish/deploy, unbounded shell loops |
| Decision engine | ordered rules → mode filter → capability filter; every decision stored with rule, reason, intended action, held-back reason, latency |
| Interventions | approvals queue with long-poll wait and expiry; operator stop/block; asynchronous pause/resume/stop for runtime-control platforms; completion review with capped redirects |
| Output evaluator | task-scoped, grounding-aware, tiered fast→deep, cached, every call costed |
| Reliability score | documented formula, per agent, "not enough data" under 3 tasks |
| Claude Code adapter | stdlib hook (fail-open, offline spool, transcript usage), companion CLI (connect / install with diff + consent + backup / uninstall / status / doctor) |
| OpenAI Agents SDK adapter | run hooks (control), tool guardrail (decision point + approvals), tracing bridge (observation), `run()` wrapper |
| Accounts | sign-up/sign-in, scrypt passwords, signed session tokens, dev token refused outside development, integration keys refused on the management API |
| Dashboard | login; overview by platform; connected agents grouped by platform with scores; live activity; sessions with tasks/decisions/approvals; tasks; approvals queue; issues; interventions log; reliability + evaluation cost; integrations with setup and limits; policies with the new controls |
| Deploy config | `railway.json`, `fly.toml`, `$PORT`-aware Dockerfile, `postgres://` normalisation, `docs/DEPLOY.md` |

## Tests

* 131 unit/API tests (backend 93, SDK 20, companion hook 18).
* Live, real-agent tests — no synthetic events:
  * Claude Code (headless `claude -p`, hooks installed by the companion):
    benign, approval, loop, stop — all pass.
  * OpenAI Agents SDK (real runs on Gemini via its OpenAI-compatible
    endpoint, helpdesk app): handoffs, approve, deny, pause/resume, stop,
    guardrail, tool failure, 4 concurrent runs — all pass.
  * Mixed: 2 Claude Code sessions + 3 Agents SDK runs at once — all
    recorded separately under the right platform, no cross-talk.
  * OpenAI's own example apps (`--app examples`) are supported by the test but
    were not run: executing downloaded third-party code needs your approval.

## Findings from real agents (and what changed because of them)

1. Claude Code on Windows uses a **PowerShell** tool; risk rules match
   PowerShell syntax (`Remove-Item -Recurse -Force`) — the approval test
   caught exactly that command.
2. A call refused by **Claude Code's own permissions** after the supervisor
   allowed it produces no PostToolUse — it looked in flight forever. Now
   closed as "not run (inferred)" when the task ends, and `PermissionDenied`
   is forwarded.
3. Hooks carry **no token usage**; the local transcript does. Now read
   incrementally, deduplicated by request id.
4. Claude Code **escaped the loop guard** by moving its retries into one
   background `until …; do …; done` command, which also kept the headless
   session alive forever. Added the `exec.unbounded_loop` risk rule and
   recording of background tasks at completion.
5. An operator stop is usually applied **before the next tool call** — with
   the result event of the current one. The hook now reports `AGENT_STOPPED`
   so the turn is recorded as cancelled, and harvests usage at SessionEnd.
6. Long Claude Code sessions made session-scoped detectors meaningless (an
   hour-old `git status` counting toward a loop). Detection is task-scoped and
   a loop requires an identical result, not just identical arguments.

## Architectural decisions

* **Session = existing `executions` table**, extended, rather than a new table:
  keeps every existing query, metric and test valid.
* **Protocol types are canonical; legacy names are translated** at the edge,
  so there is one vocabulary downstream.
* **Capabilities are enforced in the backend**, not trusted from adapters:
  `connect` drops anything the platform cannot do.
* **Operator commands bypass the supervision mode**; modes bound the
  supervisor's own authority only.
* **Fail open everywhere at the edge**, fail visible in the backend (a failed
  evaluation is recorded as "not reviewed", never silently missing).
* **Approvals are bounded** by the platform's hook timeout and resolve to a
  policy-defined default; nothing waits forever.

## Remaining work

* Deployment (needs your Vercel + Railway/Fly accounts — config is ready).
* Alembic migrations before production data matters.
* Desktop companion GUI, automated Claude Code setup from it (phase 2).
* Cross-agent analysis (conflicts, duplicated work) — phase 2.
* Running OpenAI's own example apps under the adapter (opt-in, third-party code).
* MCP, LangGraph, CrewAI, browser extension, more providers (phase 3).
* Pre-existing lint findings in older dashboard code (trace ref, policy effect).
