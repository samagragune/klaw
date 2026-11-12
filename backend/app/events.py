"""Universal event protocol - the vocabulary every integration speaks.

The protocol types (upper-case) are canonical: they are what is stored, what
the detectors read and what the dashboard renders. Every adapter - Claude Code
hooks, the OpenAI Agents SDK, the custom Python SDK - emits these.

The original Python SDK speaks an older dotted vocabulary (``tool.started``).
``LEGACY_EVENT_MAP`` translates it at ingestion so existing integrations keep
working; nothing downstream sees a legacy type.

Unknown types are rejected rather than stored, so a typo in an integration
surfaces immediately instead of producing an invisible trace.
"""
from __future__ import annotations

# --- Types required by the protocol ----------------------------------------
AGENT_REGISTERED = "AGENT_REGISTERED"
SESSION_STARTED = "SESSION_STARTED"
SESSION_ENDED = "SESSION_ENDED"
TASK_CREATED = "TASK_CREATED"
TASK_UPDATED = "TASK_UPDATED"
TASK_COMPLETED = "TASK_COMPLETED"
PROMPT_SUBMITTED = "PROMPT_SUBMITTED"
MODEL_CALL = "MODEL_CALL"
TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
SUBAGENT_STARTED = "SUBAGENT_STARTED"
SUBAGENT_COMPLETED = "SUBAGENT_COMPLETED"
AGENT_HANDOFF = "AGENT_HANDOFF"
AGENT_ERROR = "AGENT_ERROR"
AGENT_IDLE = "AGENT_IDLE"
AGENT_STOPPED = "AGENT_STOPPED"

# --- Extensions: control and supervision ------------------------------------
#: A tool call the supervisor (or an operator) refused before it ran.
TOOL_CALL_BLOCKED = "TOOL_CALL_BLOCKED"
#: Runtime confirmed it suspended / resumed at a checkpoint (runtime-control
#: platforms only - Claude Code cannot pause).
SESSION_PAUSED = "SESSION_PAUSED"
SESSION_RESUMED = "SESSION_RESUMED"
#: A guardrail evaluated (OpenAI Agents SDK input/output/tool guardrails).
GUARDRAIL_RESULT = "GUARDRAIL_RESULT"
#: A supervisor decision returned at a decision point, written into the trace
#: so the timeline shows what the supervisor said and when.
SUPERVISOR_DECISION = "SUPERVISOR_DECISION"
APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
#: Platform notices that are worth keeping but carry no semantics of their own
#: (Claude Code notifications, compaction, free-form SDK logs).
NOTIFICATION = "NOTIFICATION"
LOG = "LOG"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        AGENT_REGISTERED,
        SESSION_STARTED,
        SESSION_ENDED,
        TASK_CREATED,
        TASK_UPDATED,
        TASK_COMPLETED,
        PROMPT_SUBMITTED,
        MODEL_CALL,
        TOOL_CALL_STARTED,
        TOOL_CALL_COMPLETED,
        TOOL_CALL_FAILED,
        SUBAGENT_STARTED,
        SUBAGENT_COMPLETED,
        AGENT_HANDOFF,
        AGENT_ERROR,
        AGENT_IDLE,
        AGENT_STOPPED,
        TOOL_CALL_BLOCKED,
        SESSION_PAUSED,
        SESSION_RESUMED,
        GUARDRAIL_RESULT,
        SUPERVISOR_DECISION,
        APPROVAL_REQUESTED,
        APPROVAL_RESOLVED,
        NOTIFICATION,
        LOG,
    }
)

#: Session end states carried in ``SESSION_ENDED.payload.status``.
END_COMPLETED = "completed"
END_FAILED = "failed"
END_CANCELLED = "cancelled"

# --- Legacy SDK vocabulary --------------------------------------------------
#: legacy type -> (protocol type, payload fields to merge in)
LEGACY_EVENT_MAP: dict[str, tuple[str | None, dict]] = {
    "execution.started": (SESSION_STARTED, {}),
    "execution.completed": (SESSION_ENDED, {"status": END_COMPLETED}),
    "execution.failed": (SESSION_ENDED, {"status": END_FAILED}),
    "execution.cancelled": (SESSION_ENDED, {"status": END_CANCELLED}),
    "execution.paused": (SESSION_PAUSED, {}),
    "execution.resumed": (SESSION_RESUMED, {}),
    "tool.started": (TOOL_CALL_STARTED, {}),
    "tool.completed": (TOOL_CALL_COMPLETED, {}),
    "tool.failed": (TOOL_CALL_FAILED, {}),
    "tool.blocked": (TOOL_CALL_BLOCKED, {}),
    # The protocol records a model call once, when its usage is known.
    "llm.requested": (None, {}),
    "llm.completed": (MODEL_CALL, {"status": "ok"}),
    "llm.failed": (MODEL_CALL, {"status": "error"}),
    "checkpoint.reached": (LOG, {}),
    "agent.started": (LOG, {}),
    "agent.completed": (LOG, {}),
    "agent.failed": (AGENT_ERROR, {}),
    "intervention.requested": (LOG, {}),
    "intervention.completed": (LOG, {}),
    "log": (LOG, {}),
}

#: Every type the ingestion endpoints accept.
ACCEPTED_TYPES: frozenset[str] = EVENT_TYPES | frozenset(LEGACY_EVENT_MAP)


def normalise(event_type: str, payload: dict) -> tuple[str | None, dict]:
    """Translate a legacy type. Returns ``(None, ...)`` for events to drop."""
    if event_type in EVENT_TYPES:
        return event_type, payload
    mapped, extra = LEGACY_EVENT_MAP[event_type]
    if mapped is None:
        return None, payload
    return mapped, {**payload, **extra}


def is_model_error(event_type: str, payload: dict) -> bool:
    return event_type == MODEL_CALL and payload.get("status") == "error"
