"""Event vocabulary - mirrors ``backend/app/events.py``.

The backend rejects unknown event types, so these two lists must agree.
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
