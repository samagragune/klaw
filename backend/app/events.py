"""Canonical event vocabulary.

This list is the contract between the SDK and the backend. The SDK mirrors it
in ``sdk/reliability/events.py``; the two must stay in sync. Unknown event
types are rejected at ingestion rather than silently stored, so a typo in an
integration surfaces immediately instead of producing invisible traces.
"""
from __future__ import annotations

AGENT_STARTED = "agent.started"
AGENT_COMPLETED = "agent.completed"
AGENT_FAILED = "agent.failed"

EXECUTION_STARTED = "execution.started"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_PAUSED = "execution.paused"
EXECUTION_RESUMED = "execution.resumed"

TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
TOOL_FAILED = "tool.failed"
TOOL_BLOCKED = "tool.blocked"

LLM_REQUESTED = "llm.requested"
LLM_COMPLETED = "llm.completed"
LLM_FAILED = "llm.failed"

CHECKPOINT_REACHED = "checkpoint.reached"

INTERVENTION_REQUESTED = "intervention.requested"
INTERVENTION_COMPLETED = "intervention.completed"

LOG = "log"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        AGENT_STARTED,
        AGENT_COMPLETED,
        AGENT_FAILED,
        EXECUTION_STARTED,
        EXECUTION_COMPLETED,
        EXECUTION_FAILED,
        EXECUTION_CANCELLED,
        EXECUTION_PAUSED,
        EXECUTION_RESUMED,
        TOOL_STARTED,
        TOOL_COMPLETED,
        TOOL_FAILED,
        TOOL_BLOCKED,
        LLM_REQUESTED,
        LLM_COMPLETED,
        LLM_FAILED,
        CHECKPOINT_REACHED,
        INTERVENTION_REQUESTED,
        INTERVENTION_COMPLETED,
        LOG,
    }
)

#: Events that signal the execution has reached a terminal state.
TERMINAL_EVENTS: frozenset[str] = frozenset(
    {EXECUTION_COMPLETED, EXECUTION_FAILED, EXECUTION_CANCELLED}
)
