# Contributing to Klaw

Thanks for helping make agents safer to run. Klaw is small on purpose; contributions that keep it
honest (no claimed capability the code doesn't have) and easy to run locally are the most welcome.

## Getting set up

```bash
git clone https://github.com/samagragune/klaw.git && cd klaw
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e "./backend[dev]" -e "./sdk[dev]" -e ./companion
cp .env.example .env                               # local settings
pytest backend/tests sdk/tests companion/tests -q

cd dashboard && npm ci && npm run dev              # http://localhost:3000
```

Run the supervisor for dashboard work with `klaw-supervisor start`.

## Making a change

1. Open an issue first for anything larger than a bug fix, so we can agree on the approach.
2. Branch from `main`, keep the change focused, and add or update tests.
3. Before opening a PR:
   - `pytest backend/tests sdk/tests companion/tests -q` passes
   - `cd dashboard && npx tsc --noEmit && npm run build` passes
   - user-facing changes are reflected in the docs (`dashboard/src/components/docs-content.tsx`
     and/or `docs/`)
4. Describe *what* changed and *how you verified it* in the PR. If the change affects how Klaw
   acts on a real agent, say which live test you ran (`integrations_tests/`).

## Ground rules for the code

- **Fail open at the edge, fail visibly in the supervisor.** An adapter must never break the agent
  it watches; the supervisor must never silently drop a decision.
- **Never overstate control.** A platform's declared capabilities are the contract. If something
  can't be done on a platform, say so in the UI and docs rather than approximating it.
- **Privacy by default.** Redact secret-looking keys at the source; prefer fingerprints over
  payloads.
- **Deterministic first.** Rules decide; models may advise when the user opts in.

## Adding a platform

Adapters speak the event protocol (`/v1/events`, `/v1/decisions`). See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md#protocol) and the existing adapters in `companion/`
and `sdk/klaw/integrations/`. Declare only the capabilities you actually implement.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind.
