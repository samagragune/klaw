"""Reliability companion - connect local agent environments to the supervisor.

    python -m reliability_companion connect --api-url URL --key rl_int_...
    python -m reliability_companion claude-code install --project PATH
    python -m reliability_companion claude-code install --user
    python -m reliability_companion claude-code uninstall --project PATH
    python -m reliability_companion status
    python -m reliability_companion doctor

This is the command-line core of the desktop companion (phase 2 wraps it in a
GUI). Its rules:

* It never changes an agent's configuration without showing the exact change
  and getting an explicit yes (``--yes`` is the scripted equivalent).
* It backs up any settings file before writing it.
* It only ever touches hook entries it created itself (they are recognisable
  by the hook path), so uninstalling leaves the user's own hooks alone.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import platform
import shutil
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import __version__

HOME = Path.home() / ".reliability"
CONFIG = HOME / "config.json"
HOOK_DIR = HOME / "hooks"
HOOK_FILE = HOOK_DIR / "reliability_hook.py"
MARKER = "reliability_hook.py"

#: Claude Code events the adapter forwards, and per-event hook timeouts (s).
#: PreToolUse may wait for a human approval; Stop may run a completion review.
CLAUDE_CODE_EVENTS: dict[str, int] = {
    "SessionStart": 10,
    "UserPromptSubmit": 10,
    "PreToolUse": 600,
    "PermissionRequest": 600,
    "PermissionDenied": 10,
    "PostToolUse": 10,
    "PostToolUseFailure": 10,
    "SubagentStart": 10,
    "SubagentStop": 10,
    "Stop": 60,
    "StopFailure": 10,
    "Notification": 10,
    "PreCompact": 10,
    "SessionEnd": 10,
}

CLAUDE_CODE_CAPABILITIES = [
    "observe", "block_tool", "request_approval", "corrective_feedback", "stop",
]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def read_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config(config: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG, 0o600)  # owner-only on POSIX; profile ACLs on Windows
    except OSError:
        pass


def api(config: dict, method: str, path: str, body=None, timeout: float = 10):
    req = urllib.request.Request(
        config["api_url"].rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {config['integration_key']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing to change settings without confirmation (no terminal). Re-run with --yes.")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------
# connect / status / doctor
# --------------------------------------------------------------------------


def cmd_connect(args) -> int:
    config = read_config()
    config.update({"api_url": args.api_url.rstrip("/"), "integration_key": args.key})
    config.setdefault("capture", {
        "tool_input": True,
        "tool_output_preview": False,
        "prompts": True,
        "assistant_messages": True,
    })
    config.setdefault("timeouts", {
        "event_seconds": 4,
        "decision_seconds": 8,
        "completion_review_seconds": 45,
        "approval_max_seconds": 540,
    })
    try:
        integration = api(config, "POST", "/v1/integrations/connect", {
            "capabilities": CLAUDE_CODE_CAPABILITIES,
            "details": {
                "companion_version": __version__,
                "host": socket.gethostname(),
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
            },
        })
    except urllib.error.HTTPError as exc:
        print(f"The supervisor refused the key ({exc.code}): {exc.read().decode()[:200]}")
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Could not reach {args.api_url}: {exc}")
        return 1
    write_config(config)
    print(f"Connected to integration '{integration['name']}' ({integration['platform']}).")
    print(f"Capabilities: {', '.join(integration['capabilities'])}")
    print(f"Config saved to {CONFIG}")
    return 0


def cmd_status(args) -> int:
    config = read_config()
    if not config.get("api_url"):
        print("Not connected. Run: python -m reliability_companion connect --api-url URL --key KEY")
        return 1
    print(f"Supervisor : {config['api_url']}")
    print(f"Key        : {config['integration_key'][:11]}... (stored in {CONFIG})")
    print(f"Capture    : {json.dumps(config.get('capture', {}))}")
    for label, path in (("user", user_settings()), ("project", None)):
        if path and path.exists():
            installed = MARKER in path.read_text(encoding="utf-8")
            print(f"Claude Code ({label}) hooks: {'installed' if installed else 'not installed'} - {path}")
    spool = HOME / "spool.jsonl"
    if spool.exists():
        pending = sum(1 for _ in spool.open(encoding="utf-8"))
        print(f"Offline spool: {pending} event(s) waiting to be delivered")
    return 0


def cmd_doctor(args) -> int:
    config = read_config()
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'ok' if passed else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))

    print("Reliability companion doctor")
    check("config present", bool(config.get("api_url") and config.get("integration_key")), str(CONFIG))
    check("hook installed to ~/.reliability/hooks", HOOK_FILE.exists(), str(HOOK_FILE))
    check("python runs the hook", shutil.which(Path(sys.executable).name) is not None or Path(sys.executable).exists(), sys.executable)
    if config.get("api_url"):
        try:
            with urllib.request.urlopen(config["api_url"].rstrip("/") + "/health", timeout=5) as r:
                check("supervisor reachable", r.status == 200, config["api_url"])
        except Exception as exc:  # noqa: BLE001
            check("supervisor reachable", False, str(exc))
        try:
            api(config, "POST", "/v1/integrations/connect", {"capabilities": CLAUDE_CODE_CAPABILITIES})
            check("integration key accepted", True)
        except Exception as exc:  # noqa: BLE001
            check("integration key accepted", False, str(exc))
    claude = shutil.which("claude")
    check("claude CLI on PATH", claude is not None, claude or "not found")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# Claude Code hooks
# --------------------------------------------------------------------------


def user_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def project_settings(project: str) -> Path:
    return Path(project).resolve() / ".claude" / "settings.json"


def install_hook_file() -> Path:
    HOOK_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("hook.py"), HOOK_FILE)
    return HOOK_FILE


def hook_command() -> str:
    python = Path(sys.executable).as_posix()
    return f'"{python}" "{HOOK_FILE.as_posix()}"'


def with_hooks(settings: dict) -> dict:
    """Settings with our hook added to every forwarded event (idempotent)."""
    updated = json.loads(json.dumps(settings))
    hooks = updated.setdefault("hooks", {})
    for event, timeout in CLAUDE_CODE_EVENTS.items():
        groups = [g for g in hooks.get(event, []) if not _ours(g)]
        groups.append({"hooks": [{"type": "command", "command": hook_command(), "timeout": timeout}]})
        hooks[event] = groups
    return updated


def without_hooks(settings: dict) -> dict:
    updated = json.loads(json.dumps(settings))
    hooks = updated.get("hooks", {})
    for event in list(hooks):
        remaining = [g for g in hooks[event] if not _ours(g)]
        if remaining:
            hooks[event] = remaining
        else:
            del hooks[event]
    if not hooks:
        updated.pop("hooks", None)
    return updated


def _ours(group: dict) -> bool:
    return any(MARKER in str(h.get("command", "")) for h in group.get("hooks", []))


def _write_settings(path: Path, before: dict, after: dict, assume_yes: bool, verb: str) -> int:
    old = json.dumps(before, indent=2).splitlines()
    new = json.dumps(after, indent=2).splitlines()
    if old == new:
        print(f"No change needed in {path}.")
        return 0
    print(f"\nProposed change to {path}:\n")
    for line in difflib.unified_diff(old, new, "current", "proposed", lineterm=""):
        print("  " + line)
    print()
    if not confirm(f"{verb} Reliability hooks in this file?", assume_yes):
        print("No changes made.")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f".json.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copyfile(path, backup)
        print(f"Backed up the previous settings to {backup}")
    path.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
    print(f"{verb} done: {path}")
    return 0


def _target(args) -> Path:
    if args.user:
        return user_settings()
    if not args.project:
        raise SystemExit("Choose --project PATH or --user.")
    return project_settings(args.project)


def cmd_cc_install(args) -> int:
    if not read_config().get("api_url"):
        print("Connect first: python -m reliability_companion connect --api-url URL --key KEY")
        return 1
    install_hook_file()
    path = _target(args)
    before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    print(
        "Claude Code will run the Reliability hook on each lifecycle event. It forwards "
        "tool names and inputs (secret-looking keys redacted), prompts and final "
        "messages to your supervisor, and can block or hold tool calls according to "
        "your policy. If the supervisor is unreachable, Claude Code continues normally."
    )
    return _write_settings(path, before, with_hooks(before), args.yes, "Install")


def cmd_cc_uninstall(args) -> int:
    path = _target(args)
    if not path.exists():
        print(f"{path} does not exist; nothing to remove.")
        return 0
    before = json.loads(path.read_text(encoding="utf-8"))
    return _write_settings(path, before, without_hooks(before), args.yes, "Remove")


def cmd_setup(args) -> int:
    """Everything in one step: connect, install hooks, verify.

    This is the command the dashboard shows, with the URL and key filled in.
    """
    print("Step 1/3 - connecting to the supervisor")
    if cmd_connect(args) != 0:
        return 1
    print("\nStep 2/3 - installing Claude Code hooks")
    if not args.user and not args.project:
        args.project = os.getcwd()
    if cmd_cc_install(args) != 0:
        return 1
    print("\nStep 3/3 - checking")
    ok = cmd_doctor(args) == 0
    scope = "every project (user settings)" if args.user else args.project
    print(
        f"\n{'Done' if ok else 'Installed, but a check failed (see above)'}. "
        f"Claude Code sessions in {scope} now report to {args.api_url.rstrip('/')}.\n"
        "Start Claude Code there; the session appears on the dashboard within seconds."
    )
    return 0 if ok else 1


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reliability_companion", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("connect", help="store the supervisor URL and integration key")
    p.add_argument("--api-url", required=True)
    p.add_argument("--key", required=True, help="integration key (rl_int_...) from the dashboard")
    p.set_defaults(fn=cmd_connect)

    s = sub.add_parser("setup", help="connect + install Claude Code hooks + check, in one step")
    s.add_argument("--api-url", required=True)
    s.add_argument("--key", required=True, help="integration key (rl_int_...) from the dashboard")
    scope = s.add_mutually_exclusive_group()
    scope.add_argument("--project", help="project directory (default: the current directory)")
    scope.add_argument("--user", action="store_true", help="all projects (~/.claude/settings.json)")
    s.add_argument("--yes", action="store_true", help="apply without the interactive confirmation")
    s.set_defaults(fn=cmd_setup)

    sub.add_parser("status", help="show connection and hook state").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check that everything is wired correctly").set_defaults(fn=cmd_doctor)

    cc = sub.add_parser("claude-code", help="manage Claude Code hooks")
    cc_sub = cc.add_subparsers(dest="action", required=True)
    for name, fn in (("install", cmd_cc_install), ("uninstall", cmd_cc_uninstall)):
        a = cc_sub.add_parser(name)
        scope = a.add_mutually_exclusive_group(required=True)
        scope.add_argument("--project", help="project directory (writes <project>/.claude/settings.json)")
        scope.add_argument("--user", action="store_true", help="all projects (~/.claude/settings.json)")
        a.add_argument("--yes", action="store_true", help="apply without the interactive confirmation")
        a.set_defaults(fn=fn)

    args = parser.parse_args(argv)
    return args.fn(args)
