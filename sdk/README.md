# reliability-sdk

Observability **and execution control** for autonomous Python agents.

```bash
pip install -e ./sdk
```

## Minimal integration

```python
from reliability import Supervisor

supervisor = Supervisor(api_key="rl_live_…", agent_id="research-agent")

@supervisor.monitor(task="Research AI startups")
async def research():
    async with supervisor.tool("search", {"q": "AI startups"}) as call:
        call.set_result(await my_search("AI startups"))
    return "done"

result = await research()
```

## Why it can actually stop your agent

The SDK runs inside your coroutine and yields to the control plane at
**checkpoints**:

- entering `@supervisor.monitor`
- before every `supervisor.tool(...)`
- before every `supervisor.llm(...)`
- wherever you `await supervisor.checkpoint()`

At each one it reconciles against the control plane's desired state:

| Desired state | What happens in your code |
|---|---|
| `running` | returns immediately |
| `paused` | suspends until resumed or cancelled |
| `cancelled` | raises `ExecutionCancelled` |
| tool blocked | raises `ToolBlocked` before the call runs |

Only after enforcing does the SDK report the command as **applied**. A command
that never reaches a checkpoint stays `pending`, and the dashboard says so.

```python
from reliability import ExecutionCancelled

try:
    await research()
except ExecutionCancelled as exc:
    # Clean up if you must, then let it propagate. Swallowing it defeats
    # the cancellation.
    raise
```

## Privacy

Defaults keep payloads in your process. Only a locally computed SHA-256
fingerprint of tool arguments is transmitted — enough for the backend to detect
identical repeated calls, not enough to reconstruct them.

```python
from reliability import CaptureConfig, Supervisor

Supervisor(..., capture=CaptureConfig.metadata_only())   # names, counts, timings
Supervisor(..., capture=CaptureConfig(capture_tool_args=True))
Supervisor(..., capture=CaptureConfig.full())            # minus redacted keys
```

Keys matching `api_key`, `token`, `password`, `secret`, `authorization`,
`cookie` and similar are redacted at any depth before anything leaves the
process. Hashing happens *before* redaction, so two calls differing only by a
secret are still distinguishable to the loop detector.

## API

| Member | Purpose |
|---|---|
| `Supervisor(api_key, agent_id, api_url=, framework=, capture=)` | Connect an agent |
| `@supervisor.monitor` / `@supervisor.monitor(task=...)` | Trace one run as an execution |
| `async with supervisor.tool(name, args)` | Checkpoint + trace a tool call |
| `async with supervisor.llm(model, prompt=)` | Checkpoint + trace a model call |
| `await supervisor.checkpoint(label=)` | Explicit yield point |
| `supervisor.log(message, **fields)` | Free-form trace note |
| `await supervisor.aclose()` | Flush telemetry and close connections |

`ToolCall.set_result(value)` and `LlmCall.record_usage(prompt_tokens,
completion_tokens)` report outcomes. Token counts are only ever what you pass —
the SDK never estimates them, so every usage figure in the dashboard traces back
to a number the provider actually returned.

## Delivery guarantees

Events are queued and flushed in batches, so instrumenting a hot loop costs one
queue append rather than one HTTP request. Each event carries a client-generated
id, making a retried batch idempotent at the backend. A flush failure keeps
events queued and retries with backoff; if the queue ever overflows, the loss is
logged with a count rather than silently swallowed.

## Requirements

Python 3.10+, `httpx`. **Async only** — `@supervisor.monitor` rejects
synchronous functions, because cooperative suspension is what makes pause and
cancel real.
