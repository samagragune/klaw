<div align="center">

<img src="docs/klaw-logo.png" width="96" alt="Klaw logo: a pixel-art mechanical claw" />

# Klaw

**An agent that supervises your AI agents.**
It runs on your own machine, plans every task, checks every action, verifies every "done"
against the real diff and your tests, and acts on its own when something goes wrong.

[![CI](https://github.com/samagragune/klaw/actions/workflows/ci.yml/badge.svg)](https://github.com/samagragune/klaw/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-ff5a4f.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3d5bff)
[![Dashboard](https://img.shields.io/badge/dashboard-klaw--supervisor.vercel.app-8b5cff)](https://klaw-supervisor.vercel.app)

[Website](https://klaw-supervisor.vercel.app) · [Quickstart](https://klaw-supervisor.vercel.app/docs/quickstart) · [The Klaw agent](https://klaw-supervisor.vercel.app/docs/agent) · [Choose the brain](https://klaw-supervisor.vercel.app/docs/brain) · [Docs](https://klaw-supervisor.vercel.app/docs)

<img src="docs/images/landing.png" alt="Klaw website" width="900" />

</div>

---

## Why Klaw

Agents now run shell commands, edit code and say "done" on their own. A trace you read
afterwards doesn't stop the recursive delete that already ran, or catch the agent that edited
the test instead of fixing the code. Klaw puts **a second agent** at the point of action:

- **It plans.** When a task starts it reads the request and the project, writes down what
  done means, how to verify it and the risks, and takes a git checkpoint you can undo to.
- **It checks every action.** Before each tool call it allows, guides, blocks, asks you, or
  stops the session, with its reasoning. Your rules, written in plain words, are enforced.
- **It verifies every "done".** It reads the real `git diff`, runs your tests, and returns
  **Pass**, **Incomplete** or **Needs another attempt**. Anything but Pass sends the agent back
  with exact instructions (up to your limit, then a human takes over).
- **It acts on its own.** It watches live sessions, investigates the ones that look stuck,
  drifting or risky, and guides or stops them without waiting for you.
- **It reviews, reports and remembers.** Code review with file-and-line findings, a handover
  report when a session ends, a daily digest, and project memory built from your approvals.
- **It stays yours.** The supervisor and its model calls run on your machine with one SQLite
  file. The hosted dashboard is a static app in your browser that talks to *your* supervisor.

## Quickstart

```bash
# 1. Run the supervisor (Python 3.11+). It creates its own database and secret.
pip install "git+https://github.com/samagragune/klaw.git#subdirectory=backend"
klaw-supervisor start

# 2. Give the Klaw agent a brain (any one of these)
klaw-supervisor set-key                          # Gemini
klaw-supervisor set-brain anthropic              # Claude models
klaw-supervisor set-brain openai --model <name>  # or openrouter, groq, deepseek, custom
klaw-supervisor set-brain ollama --model qwen3:8b   # local, no key, nothing leaves your machine
```

3. Open **[klaw-supervisor.vercel.app](https://klaw-supervisor.vercel.app)** → *Get started*.
   The supervisor address should read `http://127.0.0.1:8000`; allow "access to apps on this
   device" if your browser asks. No supervisor yet? *Look around the console* opens every page
   in preview, where values read "Model not hosted yet".
4. **Integrations → Claude Code → Create key**, then in the project to supervise:

```bash
pip install "git+https://github.com/samagragune/klaw.git#subdirectory=companion"
klaw-companion setup --api-url http://127.0.0.1:8000 --key rl_int_…
```

5. **Policies → Klaw agent**: add the checks the agent may run (for example
   `tests = pytest -q`) and your rules in plain words ("Never edit files under migrations/").

For an OpenAI Agents SDK app, one line changes:

```python
from klaw.integrations.openai_agents import AgentsSupervisor

supervisor = AgentsSupervisor(app_name="support-bot")
result = await supervisor.run(triage_agent, user_input)   # instead of Runner.run
```

## What it looks like

| The agent's plan and its investigations | Overview across platforms |
|---|---|
| <img src="docs/images/agent-session.png" alt="The Klaw agent's plan and investigations for a session" /> | <img src="docs/images/overview.png" alt="Overview" /> |
| **A live key blocked, the trace, and the agent's own stop** | **The agent's settings: rules, checks, alerts** |
| <img src="docs/images/session-approval.png" alt="Session with a blocked secret" /> | <img src="docs/images/agent-policy.png" alt="Klaw agent policy" /> |

## The Klaw agent

The agent works in a loop: think, use a tool, read the result, decide, until the evidence
settles the question. It acts on evidence, never on what the supervised agent claims.

| When | What it does |
|---|---|
| A task starts | Plans: requirements, how to verify each, risks. Git checkpoint. |
| Before each tool call | Allow · guide · block · ask a human · stop, with its reasoning. |
| The agent says done | Reads the diff, runs your checks → Pass / Incomplete / Needs another attempt. |
| A session looks wrong | Investigates on its own and guides, stops, or alerts you. |
| You ask | Chat about any session; it looks things up and can act. |
| A session ends | Writes a handover report. |

**Its tools** are deliberately limited: `read_file`, `list_dir`, `search` (read-only, inside the
session's project, never `.env` or key files); read-only `git`; changes since the task's
checkpoint; `run_check` (only commands you name in the policy, by name); session and project
history; project memory; and `send_guidance`, `stop_session`, `notify_operator`.

**Also built in:** a secret-leak guard (keys and tokens in commands or file writes are
blocked), checkpoints with an operator-only undo that is itself undoable, conflict detection
when two agents edit the same files, webhook alerts to Slack or Discord, and effectiveness
stats (how often a send-back led to a pass, how often a block was accepted).

**Hard limits the agent cannot loosen:** your policy lists, an operator stop, the secret
guard, and high-risk rules. If the model is slow, rate limited or not configured, the rules
decide alone and the dashboard says so.

### Bring any brain

| Brain | Command | Notes |
|---|---|---|
| Gemini | `klaw-supervisor set-key` | default; fast model per action |
| Anthropic | `klaw-supervisor set-brain anthropic` | |
| OpenAI | `klaw-supervisor set-brain openai --model <name>` | |
| OpenRouter / Groq / DeepSeek | `klaw-supervisor set-brain openrouter --model <vendor/model>` | any OpenAI-compatible API |
| Your own endpoint | `klaw-supervisor set-brain custom --base-url https://… --model <name>` | vLLM, LM Studio, Together… |
| Ollama | `klaw-supervisor set-brain ollama --model qwen3:8b` | local, no key |

Keys are typed hidden and stored only in the supervisor's `config.env` on your machine.

## Platforms

| | Claude Code (hooks) | OpenAI Agents SDK | Python SDK |
|---|---|---|---|
| Sessions, prompts, tool calls, sub-agents, answers | ✓ | ✓ | ✓ |
| Model calls and tokens | ✓ (from local transcript) | ✓ | ✓ |
| Agent review of every tool call | ✓ | ✓ | ✓ |
| Block a tool call with a reason | ✓ | ✓ | ✓ |
| Hold for human approval | ✓ | ✓ | — |
| Verify "done" and send the work back | ✓ | ✓ (re-runs with the agent's instructions) | report only |
| Guidance delivered to the agent | ✓ | at send-back | — |
| Stop | ✓ | ✓ | ✓ |
| Pause / resume | — | ✓ | ✓ |

## How it works

```
 your machine                                                   your browser
┌───────────────────────────────────────────────────┐        ┌──────────────────────┐
│ Claude Code ──hooks──► klaw-companion ─┐          │        │ klaw-supervisor.     │
│ Agents SDK app ──► AgentsSupervisor ───┼─events──►│        │ vercel.app           │
│ Python agent ──► klaw SDK ─────────────┘          │◄──────►│ (static glass UI,    │
│                      ▲ decisions                  │        │  talks directly to   │
│               klaw-supervisor                     │        │  127.0.0.1:8000)     │
│  rules · policies · approvals · SQLite            │        └──────────────────────┘
│  Klaw agent: plan · check · verify · act ◄── your model (Gemini, OpenAI,          │
│  tools: files (read-only) · git · your checks      Anthropic, … or local Ollama)  │
└───────────────────────────────────────────────────┘
```

Adapters are thin: they translate platform events into one protocol and turn decisions back
into platform actions. Rules, detection, the agent and all history live in the supervisor.
Every adapter **fails open**: if the supervisor is down, agents keep working and queue events.

## Repository layout

| Path | What |
|---|---|
| [`backend/`](backend) | `klaw-supervisor`: FastAPI + SQLAlchemy. Protocol, detectors, risk rules, decisions, approvals, and the Klaw agent (`supervision/agent*.py`, `tool_calling.py`). |
| [`companion/`](companion) | `klaw-companion`: Claude Code hook (stdlib only) and setup CLI. |
| [`sdk/`](sdk) | `klaw`: Python SDK and the OpenAI Agents SDK adapter. |
| [`dashboard/`](dashboard) | Next.js website, docs, and the operator console (glass UI). |
| [`integrations_tests/`](integrations_tests) | Live tests against real Claude Code sessions and Agents SDK runs. |
| [`docs/`](docs) | Integration guide, deployment notes, design history. |

## Development

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e "./backend[dev]" -e "./sdk[dev]" -e ./companion
pytest backend/tests sdk/tests companion/tests -q        # 174 tests

cd dashboard && npm ci && npm run dev                    # http://localhost:3000
```

The agent's tests use a scripted model against real git repositories, so they run offline.
Copy [`.env.example`](.env.example) to `.env` for local development settings. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## Security

The agent's file tools are read-only and confined to the session's project folder; it never
reads `.env` or key files, runs only commands you allowlist, and treats everything a
supervised agent or a file says as untrusted evidence, not instructions. Please report
vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
