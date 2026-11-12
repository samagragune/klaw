#!/usr/bin/env python3
"""Claude Code hook forwarder for the Reliability supervisor.

Claude Code runs this once per lifecycle event, passing the event as JSON on
stdin. It translates the event into the universal protocol, sends it to the
supervisor, and - at decision points - turns the supervisor's answer into
Claude Code's hook output.

Design constraints, in priority order:

1. **Never break Claude Code.** Any failure (no config, backend down, bad
   response, bug in this file) ends with exit 0 and no output, which tells
   Claude Code to carry on exactly as if the hook were not installed. The
   failure is logged and the event spooled for later delivery.
2. **Never widen permissions.** ALLOW produces no output, so Claude Code's own
   permission rules still apply. Only an explicit human approval in the
   dashboard returns ``allow``.
3. **Fast.** Standard library only, one HTTP request per event, short
   timeouts. Decision points are the only blocking calls.

Configuration: ``~/.reliability/config.json`` (written by
``python -m reliability_companion connect``), overridable with
``RELIABILITY_API_URL`` / ``RELIABILITY_INTEGRATION_KEY`` /
``RELIABILITY_CONFIG``.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

HOOK_VERSION = "0.2.0"
HOME = os.path.join(os.path.expanduser("~"), ".reliability")
DEFAULT_CONFIG = os.path.join(HOME, "config.json")
SPOOL = os.path.join(HOME, "spool.jsonl")
STATE = os.path.join(HOME, "state")
LOG = os.path.join(HOME, "hook.log")
MAX_SPOOL_LINES = 5000
MAX_TEXT = 4000
REDACT = (
    "api_key", "apikey", "authorization", "password", "passwd", "secret",
    "token", "credential", "private_key", "access_key", "cookie",
)

# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{now()} {message}\n")
        if os.path.getsize(LOG) > 2_000_000:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass


def load_config() -> dict:
    path = os.environ.get("RELIABILITY_CONFIG", DEFAULT_CONFIG)
    config: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        pass
    if os.environ.get("RELIABILITY_API_URL"):
        config["api_url"] = os.environ["RELIABILITY_API_URL"]
    if os.environ.get("RELIABILITY_INTEGRATION_KEY"):
        config["integration_key"] = os.environ["RELIABILITY_INTEGRATION_KEY"]
    config.setdefault("capture", {})
    config.setdefault("timeouts", {})
    return config


def clip(value, limit: int = MAX_TEXT):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


def redact(value, depth: int = 0):
    if depth > 6:
        return "[depth]"
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if any(r in str(k).lower() for r in REDACT) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, depth + 1) for v in value[:50]]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + f"... [{len(value)} chars]"
    return value


def fingerprint(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def request(config: dict, method: str, path: str, body=None, timeout: float = 5.0):
    url = config["api_url"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config['integration_key']}",
            "Content-Type": "application/json",
            "User-Agent": f"reliability-claude-code-hook/{HOOK_VERSION}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------
# Offline spool
# --------------------------------------------------------------------------


def spool(event: dict) -> None:
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(SPOOL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError as exc:
        log(f"spool write failed: {exc}")


def flush_spool(config: dict) -> None:
    """Deliver events that were queued while the backend was unreachable."""
    if not os.path.exists(SPOOL):
        return
    claim = SPOOL + f".{os.getpid()}"
    try:
        os.replace(SPOOL, claim)  # atomic: concurrent hooks never double-send
    except OSError:
        return
    try:
        with open(claim, encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()][-MAX_SPOOL_LINES:]
        for start in range(0, len(events), 200):
            request(config, "POST", "/v1/events", {"events": events[start:start + 200]}, timeout=10)
        os.remove(claim)
        log(f"flushed {len(events)} spooled events")
    except Exception as exc:  # put them back for next time
        log(f"spool flush failed: {exc}")
        try:
            with open(claim, encoding="utf-8") as src, open(SPOOL, "a", encoding="utf-8") as dst:
                dst.write(src.read())
            os.remove(claim)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Translation: Claude Code hook input -> protocol event
# --------------------------------------------------------------------------


def agent_identity(data: dict) -> tuple[str, str]:
    project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    project = os.path.normpath(project)
    host = socket.gethostname()
    name = os.path.basename(project) or project
    return f"{host}:{project}", f"Claude Code · {name}"


def base_event(data: dict, event_type: str, payload: dict) -> dict:
    agent_id, agent_name = agent_identity(data)
    context = {
        "cwd": data.get("cwd"),
        "permission_mode": data.get("permission_mode"),
        "host": socket.gethostname(),
    }
    if data.get("agent_type"):
        context["actor"] = data.get("agent_type")
    return {
        "event_id": uuid.uuid4().hex,
        "platform": "claude_code",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "session_id": data.get("session_id") or "unknown",
        "event_type": event_type,
        "timestamp": now(),
        "task_id": data.get("prompt_id"),
        "execution_context": {k: v for k, v in context.items() if v},
        "payload": {k: v for k, v in payload.items() if v is not None},
    }


def translate(data: dict, capture: dict) -> dict | None:
    hook = data.get("hook_event_name")
    tool = data.get("tool_name")
    tool_input = data.get("tool_input")
    send_input = capture.get("tool_input", True)
    send_output = capture.get("tool_output_preview", False)
    send_prompt = capture.get("prompts", True)
    send_messages = capture.get("assistant_messages", True)

    if hook == "SessionStart":
        return base_event(data, "SESSION_STARTED", {
            "source": data.get("source"),
            "model": data.get("model"),
            "context": {"model": data.get("model")} if data.get("model") else None,
        })
    if hook == "UserPromptSubmit":
        prompt = data.get("prompt")
        return base_event(data, "PROMPT_SUBMITTED", {
            "prompt": clip(prompt) if send_prompt else None,
            "prompt_chars": len(prompt) if isinstance(prompt, str) else None,
        })
    if hook == "PreToolUse":
        return base_event(data, "TOOL_CALL_STARTED", {
            "tool_name": tool,
            "tool_call_id": data.get("tool_use_id"),
            "args_fingerprint": fingerprint(tool_input),
            "tool_input": redact(tool_input) if send_input else None,
        })
    if hook == "PostToolUse":
        response = data.get("tool_response")
        return base_event(data, "TOOL_CALL_COMPLETED", {
            "tool_name": tool,
            "tool_call_id": data.get("tool_use_id"),
            "duration_ms": data.get("duration_ms"),
            # Always sent: it is what tells a loop (same call, same answer)
            # from legitimate repetition, without sending the output itself.
            "result_fingerprint": fingerprint(response),
            "result_preview": clip(response, 1500) if send_output else None,
        })
    if hook == "PostToolUseFailure":
        error = data.get("error")
        return base_event(data, "TOOL_CALL_FAILED", {
            "tool_name": tool,
            "tool_call_id": data.get("tool_use_id"),
            "duration_ms": data.get("duration_ms"),
            "error": clip(error, 1500),
            "error_type": "interrupted" if data.get("is_interrupt") else "tool_error",
            "result_fingerprint": "error:" + fingerprint(error),
        })
    if hook == "SubagentStart":
        return base_event(data, "SUBAGENT_STARTED", {
            "subagent_id": data.get("agent_id"),
            "subagent_type": data.get("agent_type"),
        })
    if hook == "SubagentStop":
        return base_event(data, "SUBAGENT_COMPLETED", {
            "subagent_id": data.get("agent_id"),
            "subagent_type": data.get("agent_type"),
            "output": clip(data.get("last_assistant_message")) if send_messages else None,
        })
    if hook == "Stop":
        background = data.get("background_tasks") or []
        return base_event(data, "TASK_COMPLETED", {
            "output": clip(data.get("last_assistant_message")) if send_messages else None,
            "stop_hook_active": data.get("stop_hook_active"),
            # Work Claude Code left running when it ended the turn.
            "background_tasks": len(background) if isinstance(background, list) else None,
        })
    if hook == "StopFailure":
        return base_event(data, "AGENT_ERROR", {
            "error": clip(data.get("error"), 1500),
            "error_type": str(data.get("error_details") or data.get("error") or "stop_failure")[:80],
        })
    if hook == "SessionEnd":
        return base_event(data, "SESSION_ENDED", {"status": "completed", "reason": data.get("reason")})
    if hook == "Notification":
        return base_event(data, "NOTIFICATION", {
            "message": clip(data.get("message"), 500),
            "notification_type": data.get("notification_type"),
        })
    if hook == "PermissionDenied":
        return base_event(data, "TOOL_CALL_BLOCKED", {
            "tool_name": tool,
            "tool_call_id": data.get("tool_use_id"),
            "by": "claude_code_permissions",
            "reason": clip(data.get("reason") or data.get("message") or "Denied by Claude Code's permission rules", 500),
        })
    if hook == "PermissionRequest":
        return base_event(data, "APPROVAL_REQUESTED", {
            "source": "claude_code_permission",
            "tool_name": tool,
            "tool_call_id": data.get("tool_use_id"),
            "tool_input": redact(tool_input) if send_input else None,
        })
    if hook in ("PreCompact", "PostCompact"):
        return base_event(data, "LOG", {"message": f"{hook} ({data.get('trigger')})"})
    return None


# --------------------------------------------------------------------------
# Model usage from the transcript (best effort)
# --------------------------------------------------------------------------


def transcript_model_calls(data: dict, path_key: str = "transcript_path") -> list:
    """MODEL_CALL events for API requests added to the transcript since this
    session was last read.

    Hooks carry no token usage; the transcript Claude Code writes locally does.
    Its format is internal to Claude Code rather than a published API, so this
    is best effort: any parsing problem yields no events, never an error.
    """
    path = data.get(path_key)
    session = data.get("session_id") or "unknown"
    if not path or not os.path.exists(path):
        return []
    tag = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
    marker = os.path.join(STATE, f"{session}-{tag}.offset")
    requests = {}
    try:
        os.makedirs(STATE, exist_ok=True)
        offset = 0
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as fh:
                offset = int(fh.read().strip() or 0)
        if os.path.getsize(path) < offset:
            offset = 0
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
        consumed = chunk.rfind(b"\n") + 1  # whole lines only
        for line in chunk[:consumed].splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message") or {}
            usage = message.get("usage") or {}
            if not usage:
                continue
            rid = entry.get("requestId") or message.get("id") or entry.get("uuid")
            requests[rid] = {
                "model": message.get("model"),
                "prompt_tokens": int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
                "sidechain": bool(entry.get("isSidechain")),
                "request_id": rid,
            }
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(str(offset + consumed))
    except Exception as exc:  # best effort by design
        log(f"transcript usage skipped: {exc}")
        return []
    events = []
    for call in requests.values():
        event = base_event(data, "MODEL_CALL", dict(call, status="ok", source="transcript"))
        digest = hashlib.sha256(f"{session}:{call['request_id']}".encode("utf-8")).hexdigest()
        event["event_id"] = "mc-" + digest[:40]
        if call["sidechain"]:
            event["execution_context"]["actor"] = "subagent"
        events.append(event)
    return events


# --------------------------------------------------------------------------
# Decisions -> Claude Code hook output
# --------------------------------------------------------------------------


def pre_tool_output(decision: dict, data: dict, config: dict) -> dict | None:
    action = decision.get("action")
    reason = decision.get("reason") or ""
    feedback = decision.get("feedback") or reason
    tag = f"[Reliability supervisor · {decision.get('rule')}]"

    def deny(text: str) -> dict:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{tag} {text}",
        }}

    if action == "ALLOW":
        return None  # Claude Code's own permission rules decide.
    if action == "WARN":
        return {
            "systemMessage": f"{tag} {reason}",
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": f"{tag} {feedback}"},
        }
    if action in ("BLOCK", "REDIRECT", "RETRY", "PAUSE", "ESCALATE_TO_HUMAN"):
        return deny(feedback)
    if action == "STOP":
        return {"continue": False, "stopReason": f"{tag} Stopped: {reason}"}
    if action == "REQUEST_APPROVAL":
        return await_approval(decision, config, deny, tag)
    return None


def await_approval(decision: dict, config: dict, deny, tag: str) -> dict | None:
    ticket = decision.get("approval") or {}
    approval_id = ticket.get("id")
    if not approval_id:
        return deny("Approval was required but no request was created.")
    sys.stderr.write(f"{tag} Waiting for approval in the Reliability dashboard...\n")
    deadline = time.time() + float(config["timeouts"].get("approval_max_seconds", 540))
    status = "pending"
    result: dict = {}
    while time.time() < deadline:
        try:
            result = request(config, "GET", f"/v1/approvals/{approval_id}/wait?wait=25", timeout=35) or {}
        except Exception as exc:
            log(f"approval wait failed: {exc}")
            time.sleep(2)
            continue
        status = result.get("status", "pending")
        if status != "pending":
            break
    note = result.get("note")
    who = result.get("decided_by") or "an operator"
    if status == "approved":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"{tag} Approved by {who}" + (f": {note}" if note else ""),
        }}
    if status == "denied":
        return deny(f"Denied by {who}" + (f": {note}" if note else "") + ". Do not retry this action.")
    on_timeout = ticket.get("on_timeout", "deny")
    if on_timeout == "ask":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"{tag} No supervisor decision in time; asking you directly.",
        }}
    return deny("No one approved this in time, so it was not run.")


def permission_output(decision: dict, config: dict):
    """Answer one of Claude Code's own permission prompts from the dashboard."""
    if decision.get("action") != "REQUEST_APPROVAL":
        return None  # not forwarded: Claude Code asks (headless: denies) as usual
    tag = f"[Reliability supervisor · {decision.get('rule')}]"

    def deny(text: str) -> dict:
        return {"hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny", "message": f"{tag} {text}"},
        }}

    pre = await_approval(decision, config, deny, tag)
    specific = (pre or {}).get("hookSpecificOutput", {})
    if specific.get("permissionDecision") == "allow":
        return {"hookSpecificOutput": {
            "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"},
        }}
    if specific.get("hookEventName") == "PermissionRequest":
        return pre
    if specific.get("permissionDecision") == "ask":
        return None  # fall back to Claude Code's own prompt
    return deny(specific.get("permissionDecisionReason") or "Not approved.")


def stop_output(decision: dict) -> dict | None:
    action = decision.get("action")
    tag = f"[Reliability supervisor · {decision.get('rule')}]"
    if action == "REDIRECT":
        return {"decision": "block", "reason": f"{tag} {decision.get('feedback') or decision.get('reason')}"}
    if action == "STOP":
        return {"continue": False, "stopReason": f"{tag} {decision.get('reason')}"}
    return None


def acknowledge_stop(config: dict, control: dict) -> None:
    for command in control.get("commands") or []:
        if command.get("action") == "cancel":
            try:
                request(config, "POST", "/v1/interventions/ack", {
                    "intervention_id": command["intervention_id"],
                    "status": "applied",
                    "detail": {"applied_via": "claude_code continue:false"},
                }, timeout=3)
            except Exception as exc:
                log(f"ack failed: {exc}")


def report_stopped(config: dict, data: dict, reason: str) -> None:
    """Tell the supervisor the stop actually took effect in Claude Code."""
    event = base_event(data, "AGENT_STOPPED", {"reason": reason, "by": "operator"})
    try:
        request(config, "POST", "/v1/events", {"events": [event]}, timeout=3)
    except Exception as exc:
        log(f"stop report failed: {exc}")
        spool(event)


def control_output(config: dict, response: dict | None, data: dict) -> dict | None:
    """An operator stop arriving with any event ends the turn."""
    session_id = data.get("session_id")
    for control in (response or {}).get("sessions") or []:
        if control.get("session_id") == session_id and control.get("desired_state") == "cancelled":
            reason = next(
                (c.get("reason") for c in control.get("commands") or [] if c.get("reason")),
                "Stopped by an operator",
            )
            acknowledge_stop(config, control)
            report_stopped(config, data, reason)
            return {"continue": False, "stopReason": f"[Reliability supervisor] {reason}"}
    return None


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run() -> dict | None:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    config = load_config()
    if not config.get("api_url") or not config.get("integration_key"):
        log("not configured; run: python -m reliability_companion connect")
        return None

    event = translate(data, config["capture"])
    if event is None:
        return None
    hook = data.get("hook_event_name")
    timeouts = config["timeouts"]

    try:
        if hook == "PreToolUse":
            decision = request(config, "POST", "/v1/decisions", {"event": event},
                               timeout=float(timeouts.get("decision_seconds", 8)))
            output = pre_tool_output(decision or {}, data, config)
            if decision and decision.get("action") == "STOP":
                acknowledge_stop_for(config, decision)
                report_stopped(config, data, decision.get("reason") or "Stopped")
            return output
        if hook == "PermissionRequest":
            decision = request(config, "POST", "/v1/decisions", {"event": event},
                               timeout=float(timeouts.get("decision_seconds", 8)))
            return permission_output(decision or {}, config)
        if hook in ("Stop", "SubagentStop", "SessionEnd") and config["capture"].get("model_usage", True):
            calls = transcript_model_calls(data)
            if calls:
                try:
                    request(config, "POST", "/v1/events", {"events": calls},
                            timeout=float(timeouts.get("event_seconds", 4)))
                except Exception as exc:  # usage is optional; never block Stop on it
                    log(f"usage delivery failed: {exc}")
        if hook == "Stop":
            decision = request(config, "POST", "/v1/decisions", {
                "event": event, "already_redirected": bool(data.get("stop_hook_active")),
            }, timeout=float(timeouts.get("completion_review_seconds", 45)))
            return stop_output(decision or {})
        response = request(config, "POST", "/v1/events", {"events": [event]},
                           timeout=float(timeouts.get("event_seconds", 4)))
        if hook not in ("SessionEnd", "Notification"):
            flush_spool(config)
        return control_output(config, response, data)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        detail = exc.read().decode("utf-8", "replace")[:300] if isinstance(exc, urllib.error.HTTPError) else ""
        log(f"{hook}: supervisor unreachable or refused ({exc}) {detail}; failing open")
        if not isinstance(exc, urllib.error.HTTPError) or exc.code >= 500:
            spool(event)
        return None


def acknowledge_stop_for(config: dict, decision: dict) -> None:
    """A STOP decision came from an operator cancel; mark it applied."""
    try:
        state = request(config, "GET", f"/v1/executions/{decision['execution_id']}/control", timeout=3)
        acknowledge_stop(config, state or {})
    except Exception as exc:
        log(f"stop ack lookup failed: {exc}")


def main() -> int:
    try:
        output = run()
    except Exception:
        log("hook error (failing open):\n" + traceback.format_exc())
        return 0
    if output:
        sys.stdout.write(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
