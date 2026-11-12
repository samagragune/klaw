# Integrations

Each integration is a thin adapter: platform events in, supervisor decisions
out, through the [universal protocol](#protocol). Reasoning never lives in an
adapter.

---

## Claude Code

**Mechanism:** Claude Code lifecycle hooks, installed by the companion CLI into
a project's `.claude/settings.json` (or `~/.claude/settings.json` with
`--user`). One stdlib-only Python script handles every event.

### Event mapping

| Claude Code hook | Protocol event | Notes |
|---|---|---|
| `SessionStart` | `SESSION_STARTED` | source, model |
| `UserPromptSubmit` | `PROMPT_SUBMITTED` | opens a task (`task_id` = `prompt_id`) |
| `PreToolUse` | `TOOL_CALL_STARTED` → **decision** | tool name, redacted input, argument fingerprint |
| `PostToolUse` | `TOOL_CALL_COMPLETED` | result **fingerprint** only (no output by default) |
| `PostToolUseFailure` | `TOOL_CALL_FAILED` | error text |
| `PermissionDenied` | `TOOL_CALL_BLOCKED` (by Claude Code) | |
| `PermissionRequest` | `APPROVAL_REQUESTED` → **decision** | answered from the dashboard when `forward_permission_requests` is on |
| `SubagentStart` / `SubagentStop` | `SUBAGENT_STARTED` / `SUBAGENT_COMPLETED` | actor = agent type |
| `Stop` | `MODEL_CALL`s from transcript, then `TASK_COMPLETED` → **decision** | final message, background tasks still running |
| `StopFailure` | `AGENT_ERROR` | |
| `Notification`, `PreCompact` | `NOTIFICATION`, `LOG` | |
| `SessionEnd` | `MODEL_CALL`s, then `SESSION_ENDED` | |

### Decisions → hook output

| Decision | `PreToolUse` | `Stop` |
|---|---|---|
| ALLOW | *no output* — Claude Code's own permission rules still apply | no output |
| WARN | `additionalContext` (Claude sees it) + `systemMessage` (you see it) | — |
| BLOCK | `permissionDecision: deny` + reason | — |
| REQUEST_APPROVAL | hook waits on the dashboard; approved → `allow`, denied → `deny`, timeout → policy (`deny` or `ask`) | — |
| REDIRECT | — | `decision: block` + the gaps (Claude keeps working) |
| STOP | `continue: false` | `continue: false` |

An operator **stop** issued from the dashboard is applied at whichever hook
fires next — the next tool call, or even the result of the current one — and
reported back as applied.

### Privacy settings (`~/.reliability/config.json` → `capture`)

| Key | Default | Effect |
|---|---|---|
| `tool_input` | `true` | send tool inputs (secret-looking keys redacted, truncated). Needed for risk rules. Off ⇒ decisions say "unscreened". |
| `tool_output_preview` | `false` | send a truncated tool output preview. A fingerprint is always sent. |
| `prompts` | `true` | send the prompt text (task objective). Off ⇒ only its length. |
| `assistant_messages` | `true` | send final messages. Needed for output review. |
| `model_usage` | `true` | read token usage from the local transcript. |

### Failure behaviour

* Supervisor unreachable or erroring → exit 0, no output: Claude Code behaves as
  if the hook were absent. The event is appended to `~/.reliability/spool.jsonl`
  and delivered with the next successful call.
* Any bug in the hook → same: logged to `~/.reliability/hook.log`, exit 0.
* Hook timeouts: 10 s for observation events, 600 s for `PreToolUse` /
  `PermissionRequest` (approvals), 60 s for `Stop` (completion review).

### Limitations

* **No pause.** Hooks can block a call, hold it for approval, stop the turn or
  send feedback; nothing suspends a running turn.
* A long-running command is not interrupted; control applies at the next hook.
* Token usage comes from Claude Code's local transcript. Its format is not a
  published API; parsing is best effort and never blocks anything.
* Repetition inside one command (`until curl …; do sleep 1; done`) is invisible
  to tool-level loop detection. The `exec.unbounded_loop` risk rule catches the
  common shapes — found in a live test where Claude Code did exactly this.

### Troubleshooting

`python -m reliability_companion doctor` checks config, hook file, Python,
backend reachability, key validity and the `claude` CLI. `status` shows the
spool. Hook errors are in `~/.reliability/hook.log`.

---

## OpenAI Agents SDK

**Mechanism:** `reliability.integrations.openai_agents.AgentsSupervisor`.

| SDK mechanism | Used for |
|---|---|
| `RunHooks` (awaited by the runner) | control boundaries: pause waits, stop raises `RunStopped`; model calls and usage; tool results; handoffs |
| Tool input guardrail (added by `instrument()`) | the decision point: allow, reject with a message the model sees, or hold for approval |
| Tracing processor | observation only: guardrail results, span errors |

One `supervisor.run()` is one session with one task. Handoffs appear as
`AGENT_HANDOFF` events and every later event carries the active agent as its
actor. Input-guardrail tripwires are recorded as `GUARDRAIL_RESULT` and the
session ends `failed` (that is the SDK's own semantics).

### Limitations

* Control applies at run-hook boundaries; an in-flight model call is not
  interrupted.
* Hosted tools (web search, file search, code interpreter) run on OpenAI's side:
  observed through tracing, not intercepted.
* Only applications built on the OpenAI Agents SDK — not every application that
  calls OpenAI models.
* Tracing is used for observation only; no control is claimed through it.

### Using a non-OpenAI model

The adapter is model-agnostic. The live tests run the SDK on Gemini through its
OpenAI-compatible endpoint using the SDK's own `set_default_openai_client` and
`set_default_openai_api("chat_completions")`.

---

## Custom Python SDK

The original `reliability.Supervisor` (async, cooperative checkpoints) is kept
as the custom-SDK adapter. Its legacy event names are translated to the
protocol at ingestion. Supports observe, block, stop, pause/resume.

---

## Protocol

To add a platform:

1. Create an integration (`POST /v1/integrations` with a new `platform` value
   added to `Platform` and `PLATFORM_CAPABILITIES` in `backend/app/models.py`).
2. On start, `POST /v1/integrations/connect` with the capabilities you actually
   implement — the backend drops anything the platform cannot do.
3. Send events to `POST /v1/events` (batches up to 500):

```json
{
  "event_id": "unique per event (idempotency key)",
  "platform": "claude_code",
  "agent_id": "the platform's identity for the agent",
  "agent_name": "optional display name",
  "session_id": "the platform's session / run id",
  "event_type": "TOOL_CALL_STARTED",
  "timestamp": "2026-11-12T10:00:00Z",
  "task_id": "optional; groups events into one objective",
  "execution_context": {"actor": "sub-agent", "cwd": "...", "parent_session_id": "..."},
  "payload": {"tool_name": "Bash", "tool_call_id": "...", "args_fingerprint": "...", "tool_input": {}}
}
```

4. At blocking points, `POST /v1/decisions` with `{"event": …}` and map the
   returned `action` to whatever your platform can do. For
   `REQUEST_APPROVAL`, long-poll `GET /v1/approvals/{id}/wait?wait=25`.
5. For asynchronous control (pause/stop issued from the dashboard), read the
   `sessions[].desired_state` returned by `/v1/events`, or poll
   `GET /v1/executions/{execution_id}/control?wait=25`, and acknowledge with
   `POST /v1/interventions/ack` **only after** the action took effect.

Payload conventions the detectors rely on: `tool_call_id` (pairs start and
outcome), `args_fingerprint` (loop detection without sending arguments),
`result_fingerprint` (tells a loop from legitimate repetition),
`prompt_tokens`/`completion_tokens`/`model` on `MODEL_CALL`.
