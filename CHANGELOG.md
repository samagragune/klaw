# Changelog

## 0.3.0 — Klaw

- **Renamed to Klaw.** Packages are now `klaw-supervisor`, `klaw-companion` and `klaw` (SDK);
  commands `klaw-supervisor` and `klaw-companion`; environment variables `KLAW_*`.
  The old `RELIABILITY_*` variables and the `~/.reliability` folder still work.
- **Local-first supervisor.** `klaw-supervisor start` runs with zero configuration: its own data
  folder, SQLite database and generated secret. The hosted dashboard talks to it from your browser.
- **Google sign-in** alongside email, verified against Google's keys and an allowed client ID.
- **New website**: landing page, documentation, sign-in, glass design system, pixel-claw identity.

## 0.2.0 — multi-platform supervisor

- Universal event protocol, integration registry with scoped keys and declared capabilities.
- Claude Code adapter (lifecycle hooks) and OpenAI Agents SDK adapter.
- Decision engine: risk rules, loop guard, approvals, completion review; task tracking;
  reliability scores; tiered and cached output evaluation.

## 0.1.0

- Initial supervisor with cooperative pause, resume and cancel for Python agents.
