"""`reliability-supervisor` - run the supervisor on your own machine.

    reliability-supervisor start            # http://127.0.0.1:8000, data in ~/.reliability/supervisor
    reliability-supervisor start --port 9000 --allow-origin https://my-dashboard.example
    reliability-supervisor info

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
    return Path(os.environ.get("RELIABILITY_HOME", Path.home() / ".reliability")) / "supervisor"


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
        "RELIABILITY_ENV": os.environ.get("RELIABILITY_ENV", "production"),
        "SECRET_KEY": os.environ.get("SECRET_KEY") or secret_file.read_text(encoding="utf-8").strip(),
        "DATABASE_URL": os.environ.get(
            "DATABASE_URL", f"sqlite+aiosqlite:///{(data_dir / 'reliability.db').as_posix()}"
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
    evaluator = env.get("SUPERVISOR_LLM_PROVIDER") or os.environ.get("SUPERVISOR_LLM_PROVIDER", "none")
    print(
        f"""
  Reliability supervisor
  ----------------------------------------------------------------
  listening   {url}
  data        {data_dir}
  evaluator   {evaluator}  (set GEMINI_API_KEY in {data_dir / 'config.env'} to enable)

  Next: open {HOSTED_DASHBOARD}, keep the supervisor address
  {url}, create your account, then connect an agent under
  Integrations. Allow "local network access" if your browser asks.
""",
        flush=True,
    )
    import uvicorn

    uvicorn.run("reliability_supervisor.main:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from reliability_supervisor import __version__

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    print(f"version     {__version__}")
    print(f"data        {data_dir}")
    print(f"database    {data_dir / 'reliability.db'} ({'exists' if (data_dir / 'reliability.db').exists() else 'not created yet'})")
    print(f"config      {data_dir / 'config.env'}")
    print(f"dashboard   {HOSTED_DASHBOARD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reliability-supervisor", description="Run the Reliability supervisor locally.")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start the supervisor")
    start.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1: this machine only)")
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--data-dir", help="where the database and secret live (default ~/.reliability/supervisor)")
    start.add_argument("--allow-origin", action="append", help="another dashboard origin allowed to connect")
    start.set_defaults(fn=cmd_start)
    info = sub.add_parser("info", help="show where things are")
    info.add_argument("--data-dir")
    info.set_defaults(fn=cmd_info)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
