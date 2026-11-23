"""`klaw-supervisor` - run the supervisor on your own machine.

    klaw-supervisor start            # http://127.0.0.1:8000, data in ~/.klaw/supervisor
    klaw-supervisor start --port 9000 --allow-origin https://my-dashboard.example
    klaw-supervisor set-key          # the Gemini key the Klaw agent reasons with
    klaw-supervisor set-brain anthropic              # or openai --model ..., openrouter, groq,
                                     # deepseek, ollama (local, no key), custom
    klaw-supervisor info

Zero configuration: the first start creates a data directory holding the
SQLite database, a generated signing secret and an optional ``config.env``
(for ``GEMINI_API_KEY`` and other settings). Nothing leaves your machine
except what your own dashboard requests.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

#: The hosted dashboard. It is a static site: it talks to whichever supervisor
#: the operator points it at, from the operator's own browser.
HOSTED_DASHBOARD = "https://klaw-supervisor.vercel.app"
DEFAULT_ORIGINS = [HOSTED_DASHBOARD, "http://localhost:3000", "http://127.0.0.1:3000"]


def default_data_dir() -> Path:
    return Path(os.environ.get("KLAW_HOME", Path.home() / ".klaw")) / "supervisor"


def _load_env_file(path: Path) -> None:
    """KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def prepare(data_dir: Path, extra_origins: list[str]) -> dict[str, str]:
    """Create the data directory and set the environment the app reads."""
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_file = data_dir / "secret_key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            os.chmod(secret_file, 0o600)
        except OSError:
            pass
    config = data_dir / "config.env"
    if not config.exists():
        config.write_text(
            "# Optional settings for your local supervisor.\n"
            "# Uncomment to enable model-based output review and the supervisor chat:\n"
            "# GEMINI_API_KEY=\n"
            "# Extra dashboards allowed to connect (comma separated):\n"
            "# CORS_ORIGINS=\n",
            encoding="utf-8",
        )
    _load_env_file(config)

    origins = DEFAULT_ORIGINS + extra_origins
    if os.environ.get("CORS_ORIGINS"):
        origins += [o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip()]
    env = {
        "KLAW_ENV": os.environ.get("KLAW_ENV", "production"),
        "SECRET_KEY": os.environ.get("SECRET_KEY") or secret_file.read_text(encoding="utf-8").strip(),
        "DATABASE_URL": os.environ.get(
            "DATABASE_URL", f"sqlite+aiosqlite:///{(data_dir / 'klaw.db').as_posix()}"
        ),
        "CORS_ORIGINS": ",".join(dict.fromkeys(origins)),
    }
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("SUPERVISOR_LLM_PROVIDER"):
        env["SUPERVISOR_LLM_PROVIDER"] = "gemini"
        env.setdefault("SUPERVISOR_FAST_MODEL", os.environ.get("SUPERVISOR_FAST_MODEL", "gemini-3.5-flash-lite"))
    os.environ.update(env)
    return env


def cmd_start(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    env = prepare(data_dir, args.allow_origin or [])
    url = f"http://{args.host}:{args.port}"
    provider = env.get("SUPERVISOR_LLM_PROVIDER") or os.environ.get("SUPERVISOR_LLM_PROVIDER", "none")
    if provider == "none":
        agent_line = "rules only - run `klaw-supervisor set-key` to give the agent a model"
    else:
        model = os.environ.get("SUPERVISOR_MODEL", "gemini-3.5-flash")
        fast = os.environ.get("SUPERVISOR_FAST_MODEL") or model
        agent_line = f"thinking with {provider} ({fast} per action, {model} per task)"
    print(
        f"""
  Klaw supervisor
  ----------------------------------------------------------------
  listening   {url}
  data        {data_dir}
  agent       {agent_line}

  Next: open {HOSTED_DASHBOARD}, keep the supervisor address
  {url}, create your account, then connect an agent under
  Integrations. Allow "local network access" if your browser asks.
""",
        flush=True,
    )
    import uvicorn

    uvicorn.run("klaw_supervisor.main:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_set_key(args: argparse.Namespace) -> int:
    """Store the model key the Klaw agent reasons with, without echoing it."""
    import getpass

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    prepare(data_dir, [])
    key = (args.key or getpass.getpass("Gemini API key (input hidden): ")).strip()
    if not key:
        print("No key given; nothing changed.")
        return 1
    config = data_dir / "config.env"
    lines = [
        line for line in config.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("GEMINI_API_KEY=", "# GEMINI_API_KEY="))
    ]
    lines.append(f"GEMINI_API_KEY={key}")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(config, 0o600)
    except OSError:
        pass
    print(f"Saved to {config}. Restart `klaw-supervisor start` and the agent will use it.")
    return 0


#: Ready-made brains: provider, base URL, deep model, fast model.
BRAINS = {
    "gemini": ("gemini", "", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
    "openai": ("openai", "", "", ""),
    "anthropic": ("anthropic", "", "claude-sonnet-5-5", "claude-haiku-5-5"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "", ""),
    "groq": ("openai", "https://api.groq.com/openai/v1", "", ""),
    "deepseek": ("openai", "https://api.deepseek.com/v1", "deepseek-chat", "deepseek-chat"),
    "ollama": ("ollama", "http://localhost:11434/v1", "qwen3:8b", "qwen3:8b"),
    "custom": ("openai", "", "", ""),
}
BRAIN_KEYS = ("SUPERVISOR_LLM_PROVIDER", "SUPERVISOR_LLM_BASE_URL", "SUPERVISOR_MODEL",
              "SUPERVISOR_FAST_MODEL", "SUPERVISOR_LLM_API_KEY")


def _write_config(config: Path, values: dict[str, str]) -> None:
    keep = [
        line for line in config.read_text(encoding="utf-8").splitlines()
        if not any(line.strip().lstrip("# ").startswith(key + "=") for key in values)
    ]
    keep += [f"{key}={value}" for key, value in values.items() if value != ""]
    config.write_text("\n".join(keep) + "\n", encoding="utf-8")
    try:
        os.chmod(config, 0o600)
    except OSError:
        pass


def cmd_set_brain(args: argparse.Namespace) -> int:
    """Choose the model the Klaw agent thinks with, and give it your key."""
    import getpass

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    prepare(data_dir, [])
    provider, base, deep, fast = BRAINS[args.brain]
    base = args.base_url or base
    deep = args.model or deep
    fast = args.fast_model or args.model or fast
    if not deep:
        print(f"--model is required for {args.brain} (the model name at that provider).")
        return 1
    if args.brain == "custom" and not base:
        print("--base-url is required for a custom OpenAI-compatible endpoint.")
        return 1
    key = args.key or ""
    if not key and provider != "ollama":
        key = getpass.getpass(f"{args.brain} API key (input hidden): ").strip()
        if not key:
            print("No key given; nothing changed.")
            return 1
    values = {
        "SUPERVISOR_LLM_PROVIDER": provider,
        "SUPERVISOR_LLM_BASE_URL": base,
        "SUPERVISOR_MODEL": deep,
        "SUPERVISOR_FAST_MODEL": fast,
        "SUPERVISOR_LLM_API_KEY": key,
    }
    config = data_dir / "config.env"
    _write_config(config, {k: values.get(k, "") for k in BRAIN_KEYS})
    print(f"The Klaw agent will think with {args.brain}: {fast} per action, {deep} per task.")
    print(f"Saved to {config}. Restart `klaw-supervisor start` to use it.")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from klaw_supervisor import __version__

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    print(f"version     {__version__}")
    print(f"data        {data_dir}")
    print(f"database    {data_dir / 'klaw.db'} ({'exists' if (data_dir / 'klaw.db').exists() else 'not created yet'})")
    print(f"config      {data_dir / 'config.env'}")
    print(f"dashboard   {HOSTED_DASHBOARD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klaw-supervisor", description="Run the Klaw supervisor locally.")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start the supervisor")
    start.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1: this machine only)")
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--data-dir", help="where the database and secret live (default ~/.klaw/supervisor)")
    start.add_argument("--allow-origin", action="append", help="another dashboard origin allowed to connect")
    start.set_defaults(fn=cmd_start)
    set_key = sub.add_parser("set-key", help="give the Klaw agent its Gemini API key")
    set_key.add_argument("--key", help="the key (omit to type it hidden)")
    set_key.add_argument("--data-dir")
    set_key.set_defaults(fn=cmd_set_key)
    set_brain = sub.add_parser("set-brain", help="choose the model the Klaw agent thinks with")
    set_brain.add_argument("brain", choices=list(BRAINS), help="provider (ollama runs on this machine, no key)")
    set_brain.add_argument("--model", help="model name (deep tier: verification, investigations)")
    set_brain.add_argument("--fast-model", help="model for per-action checks (default: --model)")
    set_brain.add_argument("--base-url", help="OpenAI-compatible endpoint (custom, or override)")
    set_brain.add_argument("--key", help="API key (omit to type it hidden)")
    set_brain.add_argument("--data-dir")
    set_brain.set_defaults(fn=cmd_set_brain)
    info = sub.add_parser("info", help="show where things are")
    info.add_argument("--data-dir")
    info.set_defaults(fn=cmd_info)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
