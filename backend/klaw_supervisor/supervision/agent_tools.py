"""The Klaw agent's tools: how it gathers its own evidence and acts.

Safety model
------------
* **Read-only on your files.** File tools only read, and only inside the
  supervised session's project folder (its ``cwd``). Paths that escape it,
  and files that hold secrets (``.env``, keys, credentials), are refused.
* **Commands only from your allowlist.** ``run_check`` runs a command only if
  it is named in the policy's ``agent_checks`` (for example
  ``{"tests": "pytest -q"}``). The model chooses a *name*, never a command.
* **Git is read-only** (status, diff, log, show).
* Everything a tool returns is untrusted text and is labelled as such.
* Acting tools (guidance, stop, operator messages) go through the same
  intervention paths as an operator, and are recorded.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from klaw_supervisor.models import (
    AgentMemory,
    ApprovalRequest,
    Execution,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Task,
    utcnow,
)

MAX_RESULT = 6000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build", ".mypy_cache"}
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.pfx", "*.p12",
    "credentials*", "*secret*", ".npmrc", ".pypirc", ".netrc", "*.keystore",
)
GIT_COMMANDS = {
    "status": ["status", "--short", "--branch"],
    "diff": ["diff", "--stat", "--patch", "--no-color"],
    "diff_staged": ["diff", "--cached", "--stat", "--patch", "--no-color"],
    "log": ["log", "--oneline", "-n", "15"],
    "show_last_commit": ["show", "--stat", "--no-color", "HEAD"],
}


@dataclass
class ToolContext:
    session: AsyncSession
    execution: Execution
    task: Task | None
    config: dict[str, Any]
    mode: str
    capabilities: set[str]
    #: Filled by acting tools; read by the caller after the loop.
    effects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def root(self) -> Path | None:
        cwd = (self.execution.context or {}).get("cwd")
        if not cwd:
            return None
        path = Path(cwd)
        return path if path.is_dir() else None

    @property
    def project(self) -> str:
        return str((self.execution.context or {}).get("cwd") or "*")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], Awaitable[str]]

    def declaration(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _clip(text: str, limit: int = MAX_RESULT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters cut]"


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# Files (read-only, inside the project)
# ---------------------------------------------------------------------------

def _resolve(ctx: ToolContext, raw: str | None) -> Path:
    root = ctx.root
    if root is None:
        raise PermissionError(
            "This session has no project folder on this machine, so files cannot be read."
        )
    target = (root / (raw or ".")).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise PermissionError("Outside the project folder; refused.")
    name = target.name.lower()
    if any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS):
        raise PermissionError("This file may hold secrets; the supervisor does not read it.")
    return target


async def _read_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _resolve(ctx, args.get("path"))
    if not path.is_file():
        return f"No such file: {args.get('path')}"
    start = max(1, int(args.get("start_line") or 1))
    count = min(400, max(1, int(args.get("max_lines") or 200)))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Cannot read: {exc}"
    chunk = lines[start - 1 : start - 1 + count]
    body = "\n".join(f"{start + i:>5} {line}" for i, line in enumerate(chunk))
    return _clip(f"{path.name}: lines {start}-{start + len(chunk) - 1} of {len(lines)}\n{body}")


async def _list_dir(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _resolve(ctx, args.get("path"))
    if not path.is_dir():
        return f"Not a folder: {args.get('path')}"
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        if child.name in SKIP_DIRS:
            continue
        entries.append(child.name + ("/" if child.is_dir() else ""))
    return "\n".join(entries) or "(empty)"


async def _search(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = _resolve(ctx, args.get("path"))
    try:
        pattern = re.compile(str(args.get("pattern") or ""), re.IGNORECASE)
    except re.error as exc:
        return f"Bad pattern: {exc}"
    glob = str(args.get("file_glob") or "*")
    hits: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not fnmatch.fnmatch(filename, glob):
                continue
            if any(fnmatch.fnmatch(filename.lower(), s) for s in SECRET_PATTERNS):
                continue
            file = Path(dirpath) / filename
            scanned += 1
            if scanned > 3000 or file.stat().st_size > 1_000_000:
                continue
            try:
                for number, line in enumerate(file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern.search(line):
                        hits.append(f"{file.relative_to(ctx.root)}:{number}: {line.strip()[:200]}")
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n... (more matches cut)"
            except OSError:
                continue
    return "\n".join(hits) or "No matches."


# ---------------------------------------------------------------------------
# Git and checks
# ---------------------------------------------------------------------------

async def _run(argv: list[str], cwd: Path, timeout: float) -> str:
    def call() -> str:
        try:
            done = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            return f"Not installed: {argv[0]}"
        except subprocess.TimeoutExpired:
            return f"Timed out after {timeout:.0f}s."
        out = (done.stdout or "") + (("\n[stderr]\n" + done.stderr) if done.stderr else "")
        return f"exit code {done.returncode}\n{out}"

    return _clip(await asyncio.to_thread(call))


async def _git(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.root
    if root is None:
        return "No project folder on this machine."
    which = str(args.get("command") or "status")
    if which not in GIT_COMMANDS:
        return f"Unknown git command. Use one of: {', '.join(GIT_COMMANDS)}"
    argv = ["git", *GIT_COMMANDS[which]]
    if args.get("path") and which in ("diff", "diff_staged"):
        argv += ["--", str(_resolve(ctx, args["path"]).relative_to(root.resolve()))]
    return await _run(argv, root, 20)


async def _run_check(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.root
    if root is None:
        return "No project folder on this machine; checks cannot run."
    checks: dict[str, str] = ctx.config.get("agent_checks") or {}
    name = str(args.get("name") or "")
    if name not in checks:
        return (
            f"'{name}' is not an allowed check. Allowed: {', '.join(checks) or 'none configured'}. "
            "The operator configures these in the policy (agent_checks)."
        )
    timeout = float(ctx.config.get("agent_check_timeout_seconds", 90))
    return await _run(_argv(checks[name]), root, timeout)


def _argv(command: str) -> list[str]:
    """Split an operator's check command; never through a shell."""
    if os.name != "nt":
        return shlex.split(command)
    # Windows paths keep their backslashes; quotes only group.
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part
            for part in shlex.split(command, posix=False)]


# ---------------------------------------------------------------------------
# History and memory
# ---------------------------------------------------------------------------

async def _session_history(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.supervision.agent import timeline
    from klaw_supervisor.supervision.engine import load_events

    events = await load_events(ctx.session, ctx.execution.id)
    if args.get("current_task_only", True) and ctx.task is not None:
        events = [e for e in events if e.task_id == ctx.task.id]
    return timeline(events, int(args.get("limit") or 60))


async def _project_history(ctx: ToolContext, args: dict[str, Any]) -> str:
    limit = min(30, int(args.get("limit") or 12))
    cwd = (ctx.execution.context or {}).get("cwd")
    rows = (
        await ctx.session.execute(
            select(Task, Execution.context)
            .join(Execution, Execution.id == Task.execution_id)
            .where(Task.user_id == ctx.execution.user_id)
            .where(Task.execution_id != ctx.execution.id)
            .order_by(Task.started_at.desc())
            .limit(200)
        )
    ).all()
    lines = []
    for task, context in rows:
        if cwd and (context or {}).get("cwd") != cwd:
            continue
        verdict = (task.evaluation or {}).get("verdict")
        lines.append(
            f"- {task.started_at:%Y-%m-%d} [{task.status}{', verdict ' + verdict if verdict else ''}] "
            f"{(task.objective or '')[:160]}"
        )
        if len(lines) >= limit:
            break
    approvals = (
        await ctx.session.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.user_id == ctx.execution.user_id)
            .where(ApprovalRequest.status.in_(("approved", "denied")))
            .order_by(ApprovalRequest.created_at.desc())
            .limit(10)
        )
    ).scalars()
    human = [
        f"- operator {a.status} {a.tool_name}: {str(a.action_preview)[:140]}"
        + (f" (note: {a.note})" if a.note else "")
        for a in approvals
    ]
    memory = await project_memory(ctx.session, ctx.execution.user_id, ctx.project)
    return (
        "PAST TASKS IN THIS PROJECT:\n" + ("\n".join(lines) or "- none")
        + "\n\nRECENT OPERATOR APPROVALS/DENIALS:\n" + ("\n".join(human) or "- none")
        + "\n\nWHAT YOU HAVE LEARNED ABOUT THIS PROJECT:\n" + ("\n".join(f"- {m}" for m in memory) or "- nothing yet")
    )


async def project_memory(session: AsyncSession, user_id: str, project: str) -> list[str]:
    rows = (
        await session.execute(
            select(AgentMemory.note)
            .where(AgentMemory.user_id == user_id)
            .where(AgentMemory.project.in_((project, "*")))
            .order_by(AgentMemory.created_at.desc())
            .limit(20)
        )
    ).scalars()
    return list(rows)


async def _remember(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.supervision.agent import remember

    note = str(args.get("note") or "").strip()[:400]
    if not note:
        return "Nothing to remember."
    if args.get("scope") == "project":
        existing = await project_memory(ctx.session, ctx.execution.user_id, ctx.project)
        if note not in existing:
            ctx.session.add(AgentMemory(user_id=ctx.execution.user_id, project=ctx.project,
                                        note=note, source=ctx.execution.id))
        return "Saved to project memory (used in future sessions)."
    remember(ctx.execution, note)
    return "Saved to this session's notes."


async def _update_plan(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.supervision.agent import save_plan

    save_plan(ctx.execution, ctx.task, {
        "requirements": [str(r) for r in (args.get("requirements") or [])][:12],
        "acceptance_checks": [str(r) for r in (args.get("acceptance_checks") or [])][:12],
        "risks": [str(r) for r in (args.get("risks") or [])][:8],
        "progress": str(args.get("progress") or ""),
    })
    return "Plan saved."


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------

async def _send_guidance(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.models import SupervisionMode
    from klaw_supervisor.supervision.agent import queue_guidance

    text = str(args.get("message") or "").strip()
    if not text:
        return "Empty message."
    if ctx.mode == SupervisionMode.OBSERVE:
        return "Observe mode: the agent may not send guidance. Recorded only."
    queue_guidance(ctx.execution, text, "agent")
    ctx.effects.append({"type": "guidance", "message": text})
    return "Queued: the supervised agent receives it before its next step."


async def _stop_session(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.control import create_intervention
    from klaw_supervisor.models import SupervisionMode

    reason = str(args.get("reason") or "").strip() or "Stopped by the Klaw agent"
    if ctx.mode != SupervisionMode.CONTROL:
        ctx.effects.append({"type": "stop_recommended", "reason": reason})
        return f"Mode is {ctx.mode}: the agent may only recommend a stop. Recorded for the operator."
    try:
        await create_intervention(ctx.session, execution=ctx.execution, action="cancel",
                                  reason=f"Klaw agent: {reason}", requested_by="klaw-agent")
    except ValueError as exc:
        return f"Could not stop: {exc}"
    ctx.effects.append({"type": "stop", "reason": reason})
    return "Stop requested. It applies at the agent's next step."


async def _notify_operator(ctx: ToolContext, args: dict[str, Any]) -> str:
    message = str(args.get("message") or "").strip()
    if not message:
        return "Empty message."
    severity = {"low": IncidentSeverity.LOW, "high": IncidentSeverity.HIGH}.get(
        str(args.get("severity") or "").lower(), IncidentSeverity.MEDIUM
    )
    ctx.session.add(Incident(
        execution_id=ctx.execution.id, agent_id=ctx.execution.agent_id,
        user_id=ctx.execution.user_id, task_id=ctx.task.id if ctx.task else None,
        category="agent_message", source="klaw-agent", status=IncidentStatus.OPEN,
        severity=severity, title=message[:120], description=message, evidence={},
        recommended_action=str(args.get("suggested_action") or "") or None,
    ))
    ctx.effects.append({"type": "notify", "message": message})
    return "The operator has been notified on the dashboard."


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

S = {"type": "string"}
I = {"type": "integer"}
LIST = {"type": "array", "items": {"type": "string"}}

READ_FILE = Tool("read_file", "Read a text file in the project (line numbers included).",
                 _schema({"path": S, "start_line": I, "max_lines": I}, ["path"]), _read_file)
LIST_DIR = Tool("list_dir", "List a folder in the project.", _schema({"path": S}), _list_dir)
SEARCH = Tool("search", "Search project files for a regular expression.",
              _schema({"pattern": S, "path": S, "file_glob": S}, ["pattern"]), _search)
GIT = Tool("git", "Read-only git in the project: status, diff, diff_staged, log, show_last_commit. "
           "Use diff to see exactly what the agent changed.",
           _schema({"command": {"type": "string", "enum": list(GIT_COMMANDS)}, "path": S}, ["command"]), _git)
RUN_CHECK = Tool("run_check", "Run one of the operator's allowlisted checks by name (for example 'tests') "
                 "and get its output and exit code. Use it to verify claims instead of trusting them.",
                 _schema({"name": S}, ["name"]), _run_check)
SESSION_HISTORY = Tool("session_history", "What the supervised agent did in this session.",
                       _schema({"limit": I, "current_task_only": {"type": "boolean"}}), _session_history)
PROJECT_HISTORY = Tool("project_history", "Past tasks in this project with their verdicts, recent operator "
                       "approvals/denials, and what you learned about this project before.",
                       _schema({"limit": I}), _project_history)
REMEMBER = Tool("remember", "Keep a note. scope 'session' for this session, 'project' to remember it in "
                "future sessions of this project (lessons, conventions, what the operator wants).",
                _schema({"note": S, "scope": {"type": "string", "enum": ["session", "project"]}}, ["note"]), _remember)
UPDATE_PLAN = Tool("update_plan", "Record your plan for supervising this task: the user's requirements, how "
                   "to verify them, risks, and progress so far.",
                   _schema({"requirements": LIST, "acceptance_checks": LIST, "risks": LIST, "progress": S}), _update_plan)
SEND_GUIDANCE = Tool("send_guidance", "Send the supervised agent an instruction it receives before its next step.",
                     _schema({"message": S}, ["message"]), _send_guidance)
STOP_SESSION = Tool("stop_session", "Stop the supervised session (serious harm or hopelessly off task).",
                    _schema({"reason": S}, ["reason"]), _stop_session)
NOTIFY_OPERATOR = Tool("notify_operator", "Tell the human operator something on the dashboard.",
                       _schema({"message": S, "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                                "suggested_action": S}, ["message"]), _notify_operator)

async def _changes_since_task_start(ctx: ToolContext, args: dict[str, Any]) -> str:
    from klaw_supervisor.supervision.agent_features import diff_since

    return await diff_since(ctx.execution, stat_only=bool(args.get("stat_only")))


CHANGES = Tool("changes_since_task_start", "Everything changed in the project since the checkpoint Klaw "
               "took when this task started (more precise than git diff when the tree was already dirty), "
               "plus new untracked files.", _schema({"stat_only": {"type": "boolean"}}), _changes_since_task_start)

INVESTIGATE = [READ_FILE, LIST_DIR, SEARCH, GIT, CHANGES, RUN_CHECK, SESSION_HISTORY, PROJECT_HISTORY,
               REMEMBER, UPDATE_PLAN]
ACT = [SEND_GUIDANCE, STOP_SESSION, NOTIFY_OPERATOR]
