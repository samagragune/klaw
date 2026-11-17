# klaw-companion

Connects Claude Code on your machine to a Klaw supervisor.

```bash
pip install "git+https://github.com/samagragune/klaw.git#subdirectory=companion"
klaw-companion setup --api-url http://127.0.0.1:8000 --key rl_int_…   # in your project
klaw-companion doctor
klaw-companion claude-code uninstall --project .
```

The hook (`klaw_companion/hook.py`) uses only the Python standard library and always fails open:
if the supervisor is unreachable, Claude Code behaves exactly as if the hook weren't installed and
events are queued in `~/.klaw/spool.jsonl`. See the [Claude Code guide](https://klaw-supervisor.vercel.app/docs/claude-code).
