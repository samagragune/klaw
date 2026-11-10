"""Exceptions raised into supervised agent code."""
from __future__ import annotations


class ReliabilityError(Exception):
    """Base class for every error this SDK raises."""


class ConfigurationError(ReliabilityError):
    """The SDK was constructed without something it needs."""


class TransportError(ReliabilityError):
    """The backend could not be reached or rejected a request."""


class ExecutionCancelled(ReliabilityError):
    """Raised at a checkpoint when the control plane cancelled the execution.

    Catch this only to clean up. Re-raise it, or let it propagate: swallowing
    it defeats the cancellation and the dashboard will correctly report that
    the runtime never confirmed the stop.
    """

    def __init__(self, reason: str | None = None, intervention_id: str | None = None):
        super().__init__(reason or "Execution cancelled by Reliability")
        self.reason = reason
        self.intervention_id = intervention_id


class ToolBlocked(ReliabilityError):
    """Raised when a tool call is blocked by an active control command."""

    def __init__(self, tool_name: str, reason: str | None = None):
        super().__init__(reason or f"Tool '{tool_name}' is blocked by Reliability")
        self.tool_name = tool_name
        self.reason = reason
