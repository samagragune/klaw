"""Pydantic request/response models - the wire contract for SDK and dashboard."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events import EVENT_TYPES


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class AgentRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    framework: str = Field(default="custom", max_length=80)
    description: str | None = None
    sdk_version: str | None = Field(default=None, max_length=40)


class AgentOut(ORMModel):
    id: str
    name: str
    framework: str
    status: str
    description: str | None
    sdk_version: str | None
    created_at: datetime
    last_seen_at: datetime | None


class AgentSummary(AgentOut):
    """Agent plus rollups the overview table needs."""

    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    running_count: int = 0
    open_incident_count: int = 0
    success_rate: float | None = None
    total_tokens: int = 0
    estimated_cost: float = 0.0
    last_activity_at: datetime | None = None


# ---------------------------------------------------------------------------
# Executions and events
# ---------------------------------------------------------------------------

class ExecutionStartRequest(BaseModel):
    agent_id: str
    task: str | None = None
    #: Optional client-chosen id, so the SDK can reference the execution
    #: before the HTTP round trip completes.
    execution_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionOut(ORMModel):
    id: str
    agent_id: str
    status: str
    task: str | None
    started_at: datetime
    completed_at: datetime | None
    event_count: int
    tool_call_count: int
    error_count: int
    prompt_tokens: int
    completion_tokens: int
    token_usage: int
    estimated_cost: float | None
    cost_complete: bool
    error: str | None
    output_summary: str | None


class ExecutionDetail(ExecutionOut):
    agent_name: str
    events: list["EventOut"] = Field(default_factory=list)
    incidents: list["IncidentOut"] = Field(default_factory=list)
    interventions: list["InterventionOut"] = Field(default_factory=list)


class EventIn(BaseModel):
    client_event_id: str = Field(max_length=64)
    seq: int = Field(ge=0)
    event_type: str
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"unknown event_type: {value!r}")
        return value


class EventBatchIn(BaseModel):
    execution_id: str
    events: list[EventIn] = Field(min_length=1, max_length=500)


class EventOut(ORMModel):
    id: str
    seq: int
    event_type: str
    timestamp: datetime
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------

class CommandOut(BaseModel):
    """A control command handed to the SDK runtime."""

    intervention_id: str
    action: str
    reason: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ControlStateOut(BaseModel):
    """Authoritative control state for one execution.

    ``desired_state`` is what the backend wants the runtime to be doing;
    ``commands`` are the individual instructions that still need an
    acknowledgement. The SDK treats ``desired_state`` as the source of truth
    at every checkpoint.
    """

    execution_id: str
    desired_state: Literal["running", "paused", "cancelled"]
    commands: list[CommandOut] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    control: ControlStateOut


class InterventionAck(BaseModel):
    """Runtime confirmation that a command was actually applied."""

    intervention_id: str
    status: Literal["acknowledged", "applied", "failed"]
    detail: dict[str, Any] = Field(default_factory=dict)


class InterventionCreate(BaseModel):
    action: Literal["pause", "resume", "cancel", "block_tool", "request_approval"]
    reason: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    incident_id: str | None = None


class InterventionOut(ORMModel):
    id: str
    execution_id: str
    incident_id: str | None
    action: str
    status: str
    reason: str | None
    requested_by: str
    parameters: dict[str, Any]
    created_at: datetime
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    applied_at: datetime | None
    runtime_result: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

class IncidentOut(ORMModel):
    id: str
    execution_id: str
    agent_id: str
    severity: str
    category: str
    title: str
    description: str
    evidence: dict[str, Any]
    source: str
    diagnosis: str | None
    diagnosis_source: str | None
    recommended_action: str | None
    action_taken: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class IncidentDetail(IncidentOut):
    agent_name: str
    execution_status: str
    execution_task: str | None


class IncidentUpdate(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

class PolicyConfiguration(BaseModel):
    """Deterministic limits. ``None`` disables a limit entirely."""

    max_duration_seconds: int | None = Field(default=300, ge=1)
    max_tool_calls: int | None = Field(default=50, ge=1)
    max_tokens: int | None = Field(default=100_000, ge=1)
    max_cost_usd: float | None = Field(default=1.0, ge=0)
    repeated_tool_call_threshold: int | None = Field(default=3, ge=2)
    repeated_error_threshold: int | None = Field(default=3, ge=2)
    auto_retry_enabled: bool = False
    max_retries: int = Field(default=1, ge=0, le=5)
    #: Intervention actions autopilot is allowed to take without a human.
    permitted_interventions: list[str] = Field(default_factory=lambda: ["pause"])
    #: Incident categories that must go to a human even in autopilot.
    require_approval_for: list[str] = Field(default_factory=list)


class PolicyUpsert(BaseModel):
    agent_id: str | None = None
    name: str = "default"
    supervision_mode: Literal["observe", "assist", "autopilot"] = "assist"
    configuration: PolicyConfiguration = Field(default_factory=PolicyConfiguration)


class PolicyOut(ORMModel):
    id: str
    agent_id: str | None
    name: str
    supervision_mode: str
    configuration: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Overview metrics
# ---------------------------------------------------------------------------

class OverviewMetrics(BaseModel):
    total_agents: int
    running_agents: int
    running_executions: int
    executions_total: int
    executions_succeeded: int
    executions_failed: int
    executions_cancelled: int
    open_incidents: int
    total_tokens: int
    estimated_cost: float
    #: True when every cost figure above was derived from a known model price.
    cost_complete: bool


class ApiKeyOut(ORMModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    #: Returned exactly once, at creation.
    key: str


ExecutionDetail.model_rebuild()
