"""More of what the Klaw agent does beyond deciding on tool calls.

* **Secret-leak guard** - keys and tokens in a command or a file write are
  stopped before they land in a file, a commit, a URL or a log.
* **Checkpoints** - a snapshot of the project when each task starts, so the
  operator can see everything changed since, and undo it.
* **Conflict detection** - two live agents editing the same files in the same
  project are both told, and the operator is alerted.
* **Learning from the operator** - every approval or denial (with its note)
  becomes something the agent remembers about the project.
* **Webhooks** - blocks, stops, escalations and approvals that need a human
  are pushed to Slack, Discord or any URL.
* **Digest and effectiveness** - what happened across all agents, and
  whether the agent's interventions actually helped.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from klaw_supervisor import events as E
from klaw_supervisor.models import (
    AgentMemory,
    ApprovalRequest,
    EvaluatorCall,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    SupervisorDecision,
    Task,
    utcnow,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret-leak guard
# ---------------------------------------------------------------------------

SECRET_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("private_key", "a private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws_key", "an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", "a GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("anthropic_key", "an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", "an OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("google_key", "a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", "a Stripe live key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b")),
    ("slack_token", "a Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("jwt", "a signed token (JWT)", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("db_url_password", "a database URL with a password",
     re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/@]+:[^\s@/]{4,}@")),
]


@dataclass
class SecretFinding:
    rule: str
    description: str
    redacted: str


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten(v) for v in value)
    return "" if value is None else str(value)


def scan_secrets(value: Any) -> list[SecretFinding]:
    text = _flatten(value)
    found = []
    for rule, description, pattern in SECRET_RULES:
        match = pattern.search(text)
        if match:
            token = match.group(0)
            found.append(SecretFinding(rule, description, token[:6] + "..." + token[-2:]))
    return found


# ---------------------------------------------------------------------------
# Checkpoints (git snapshots; tracked files)
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str, timeout: float = 20) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _project_dir(execution: Execution) -> Path | None:
    cwd = (execution.context or {}).get("cwd")
    path = Path(cwd) if cwd else None
    return path if path and path.is_dir() else None


async def create_checkpoint(execution: Execution, label: str) -> dict[str, Any] | None:
    """Snapshot tracked files without touching the working tree or the stash list."""
    from klaw_supervisor.supervision.agent import _save, _state

    root = _project_dir(execution)
    if root is None:
        return None
    code, out = await asyncio.to_thread(_git, root, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return None
    # `stash create` writes a commit object for uncommitted changes and
    # prints its id; it prints nothing when the tree is clean.
    _, sha = await asyncio.to_thread(_git, root, "stash", "create", f"klaw checkpoint: {label}")
    sha = sha.strip()
    if not sha:
        _, sha = await asyncio.to_thread(_git, root, "rev-parse", "HEAD")
        sha = sha.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        return None
    # Keep the object reachable so git gc never removes it.
    await asyncio.to_thread(_git, root, "update-ref", f"refs/klaw/checkpoints/{sha[:12]}", sha)
    checkpoint = {"sha": sha, "label": label, "at": utcnow().isoformat()}
    state = _state(execution)
    state["checkpoints"] = (list(state.get("checkpoints") or []) + [checkpoint])[-20:]
    _save(execution, state)
    return checkpoint


def checkpoints(execution: Execution) -> list[dict[str, Any]]:
    from klaw_supervisor.supervision.agent import _state

    return list(_state(execution).get("checkpoints") or [])


async def diff_since(execution: Execution, sha: str | None = None, stat_only: bool = False) -> str:
    root = _project_dir(execution)
    points = checkpoints(execution)
    if root is None or not points:
        return "No checkpoint for this session."
    sha = sha or points[-1]["sha"]
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return "Bad checkpoint id."
    args = ["diff", "--stat", sha] if stat_only else ["diff", "--stat", "--patch", "--no-color", sha]
    _, out = await asyncio.to_thread(_git, root, *args)
    _, untracked = await asyncio.to_thread(_git, root, "ls-files", "--others", "--exclude-standard")
    if untracked.strip():
        out += "\nNew untracked files:\n" + untracked
    return out[:12000] or "No changes since the checkpoint."


async def restore_checkpoint(execution: Execution, sha: str) -> dict[str, Any]:
    """Put tracked files back as they were at the checkpoint (operator only).

    The current state is checkpointed first, so a restore can itself be
    undone. New untracked files are left alone and listed.
    """
    root = _project_dir(execution)
    if root is None:
        raise ValueError("This session has no project folder on this machine.")
    if sha not in {c["sha"] for c in checkpoints(execution)}:
        raise ValueError("Unknown checkpoint for this session.")
    before = await create_checkpoint(execution, "before restore")
    code, out = await asyncio.to_thread(_git, root, "checkout", sha, "--", ".")
    if code != 0:
        raise ValueError(f"git could not restore: {out[:300]}")
    _, untracked = await asyncio.to_thread(_git, root, "ls-files", "--others", "--exclude-standard")
    return {
        "restored_to": sha,
        "undo_checkpoint": before["sha"] if before else None,
        "untracked_left_in_place": [f for f in untracked.splitlines() if f.strip()][:50],
    }


# ---------------------------------------------------------------------------
# Conflicts between live agents in one project
# ---------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _edited_files(events: list[ExecutionEvent], since) -> set[str]:
    files = set()
    for e in events:
        if e.event_type != E.TOOL_CALL_STARTED or e.timestamp < since:
            continue
        payload = e.payload or {}
        if payload.get("tool_name") in EDIT_TOOLS:
            tool_input = payload.get("tool_input") or {}
            path = tool_input.get("file_path") or tool_input.get("notebook_path")
            if path:
                files.add(str(path).replace("\\", "/"))
    return files


async def detect_conflicts(session: AsyncSession, user_id: str | None = None) -> list[dict[str, Any]]:
    """Live sessions in the same folder editing the same files in the last 30 minutes."""
    from klaw_supervisor.supervision.agent import _save, _state, queue_guidance
    from klaw_supervisor.supervision.engine import load_events

    query = select(Execution).where(Execution.status.in_(tuple(ExecutionStatus.LIVE)))
    if user_id:
        query = query.where(Execution.user_id == user_id)
    live = list((await session.execute(query)).scalars())
    by_project: dict[tuple[str, str], list[Execution]] = {}
    for execution in live:
        cwd = (execution.context or {}).get("cwd")
        if cwd:
            by_project.setdefault((execution.user_id, cwd), []).append(execution)
    since = utcnow() - timedelta(minutes=30)
    found = []
    for (owner, cwd), group in by_project.items():
        if len(group) < 2:
            continue
        edits = {x.id: _edited_files(await load_events(session, x.id), since) for x in group}
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                shared = sorted(edits[a.id] & edits[b.id])
                if not shared:
                    continue
                key = f"{a.id}:{b.id}:{','.join(shared)}"
                state = _state(a)
                if key in (state.get("conflicts_warned") or []):
                    continue
                state["conflicts_warned"] = (list(state.get("conflicts_warned") or []) + [key])[-20:]
                _save(a, state)
                files = ", ".join(shared[:5])
                for me, other in ((a, b), (b, a)):
                    queue_guidance(me, f"Another agent is editing the same files in this project ({files}). "
                                       "Re-read them before changing them, and avoid overwriting its work.",
                                   "conflict")
                incident = Incident(
                    execution_id=a.id, agent_id=a.agent_id, user_id=owner, category="agent_conflict",
                    source="klaw-agent", status=IncidentStatus.OPEN, severity=IncidentSeverity.HIGH,
                    title=f"Two agents are editing the same files: {files}"[:200],
                    description=f"Sessions {a.id} and {b.id} both edited {files} in {cwd} within 30 minutes.",
                    evidence={"sessions": [a.id, b.id], "files": shared},
                )
                session.add(incident)
                found.append({"project": cwd, "sessions": [a.id, b.id], "files": shared})
                await notify_webhook(session, owner, f"Klaw: two agents are editing the same files in {cwd}: {files}")
    return found


# ---------------------------------------------------------------------------
# Learning from the operator
# ---------------------------------------------------------------------------

async def learn_from_approval(session: AsyncSession, approval: ApprovalRequest) -> None:
    execution = await session.get(Execution, approval.execution_id)
    project = str((execution.context or {}).get("cwd") or "*") if execution else "*"
    what = ", ".join(f"{k}={str(v)[:80]}" for k, v in (approval.action_preview or {}).items())[:200]
    note = (
        f"Operator {approval.status} {approval.tool_name} ({what})"
        + (f" - their note: {approval.note}" if approval.note else "")
        + f". The rule that asked was: {approval.reason[:160]}"
    )
    session.add(AgentMemory(user_id=approval.user_id, project=project, note=note[:400],
                            source=approval.execution_id))


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

async def _webhook_url(session: AsyncSession, user_id: str) -> str | None:
    from klaw_supervisor.supervision.engine import effective_policy

    _, config, _ = await effective_policy(session, user_id=user_id, agent_id="", platform=None)
    url = str(config.get("notify_webhook_url") or "").strip()
    return url if url.startswith(("https://", "http://")) else None


_pending: set[asyncio.Task] = set()


async def notify_webhook(session: AsyncSession, user_id: str, text: str) -> None:
    """Fire-and-forget; a slow or failing webhook never delays a decision.

    The body carries both ``text`` (Slack) and ``content`` (Discord).
    """
    url = await _webhook_url(session, user_id)
    if not url:
        return

    async def send() -> None:
        with contextlib.suppress(Exception):
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={"text": text[:1900], "content": text[:1900]})

    task = asyncio.create_task(send())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


# ---------------------------------------------------------------------------
# Digest and effectiveness
# ---------------------------------------------------------------------------

async def digest(session: AsyncSession, user_id: str, hours: int = 24) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=hours)
    sessions = list((await session.execute(
        select(Execution).where(Execution.user_id == user_id).where(Execution.started_at >= since)
    )).scalars())
    tasks = list((await session.execute(
        select(Task).where(Task.user_id == user_id).where(Task.started_at >= since)
    )).scalars())
    decisions = (await session.execute(
        select(SupervisorDecision.action, SupervisorDecision.rule, func.count())
        .where(SupervisorDecision.user_id == user_id).where(SupervisorDecision.created_at >= since)
        .group_by(SupervisorDecision.action, SupervisorDecision.rule)
    )).all()
    incidents = list((await session.execute(
        select(Incident).where(Incident.user_id == user_id).where(Incident.created_at >= since)
        .order_by(Incident.created_at.desc())
    )).scalars())
    cost = (await session.execute(
        select(func.count(), func.coalesce(func.sum(EvaluatorCall.estimated_cost), 0.0),
               func.coalesce(func.sum(EvaluatorCall.prompt_tokens + EvaluatorCall.completion_tokens), 0))
        .where(EvaluatorCall.user_id == user_id).where(EvaluatorCall.created_at >= since)
    )).one()
    verdicts: dict[str, int] = {}
    for t in tasks:
        v = (t.evaluation or {}).get("verdict")
        if v:
            verdicts[v] = verdicts.get(v, 0) + 1
    actions: dict[str, int] = {}
    for action, _rule, count in decisions:
        actions[action] = actions.get(action, 0) + count
    return {
        "hours": hours,
        "sessions": len(sessions),
        "by_platform": {p: sum(1 for s in sessions if s.platform == p) for p in {s.platform for s in sessions}},
        "tasks": len(tasks),
        "task_status": {st: sum(1 for t in tasks if t.status == st) for st in {t.status for t in tasks}},
        "verdicts": verdicts,
        "decisions": actions,
        "incidents": [{"title": i.title, "severity": i.severity, "category": i.category,
                       "execution_id": i.execution_id} for i in incidents[:15]],
        "agent_cost": {"model_calls": cost[0], "usd": round(float(cost[1] or 0), 4), "tokens": int(cost[2] or 0)},
        "effectiveness": await effectiveness(session, user_id, hours),
    }


async def effectiveness(session: AsyncSession, user_id: str, hours: int = 168) -> dict[str, Any]:
    """Did the agent's interventions help?

    * a task sent back that later passed = a redirect that worked
    * a blocked call that was not attempted again in the same task = a block
      the agent accepted
    """
    since = utcnow() - timedelta(hours=hours)
    rows = list((await session.execute(
        select(SupervisorDecision).where(SupervisorDecision.user_id == user_id)
        .where(SupervisorDecision.created_at >= since).where(SupervisorDecision.rule.like("agent.%"))
        .order_by(SupervisorDecision.created_at)
    )).scalars())
    by_task: dict[str, list[SupervisorDecision]] = {}
    for d in rows:
        if d.task_id:
            by_task.setdefault(d.task_id, []).append(d)
    redirected = recovered = 0
    blocked = repeated = 0
    for decisions in by_task.values():
        completions = [d for d in decisions if d.rule.startswith("agent.completion")]
        if any(d.action == "REDIRECT" for d in completions):
            redirected += 1
            if completions and completions[-1].rule == "agent.completion.pass":
                recovered += 1
        blocks = [d for d in decisions if d.rule == "agent.block"]
        for block in blocks:
            blocked += 1
            later = [d for d in decisions if d.created_at > block.created_at and d.tool_name == block.tool_name
                     and d.rule == "agent.block"]
            repeated += 1 if later else 0
    return {
        "tasks_sent_back": redirected,
        "sent_back_then_passed": recovered,
        "send_back_success_rate": round(recovered / redirected, 2) if redirected else None,
        "blocks": blocked,
        "blocks_retried_anyway": repeated,
        "block_acceptance_rate": round(1 - repeated / blocked, 2) if blocked else None,
    }
