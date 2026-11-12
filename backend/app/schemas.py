"""Pydantic request/response models - the wire contract for SDK and dashboard."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events import ACCEPTED_TYPES as EVENT_TYPES


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
    platform: str = "custom_sdk"
    integration_id: str | None = None
    external_id: str | None = None


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
    #: See supervision/reliability.py for the formula. None = not enough data.
    reliability_score: float | None = None
    reliability: dict[str, Any] | None = None


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
    last_event_at: datetime | None
    last_heartbeat_at: datetime | None
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
    platform: str = "custom_sdk"
    integration_id: str | None = None
    external_session_id: str | None = None
    parent_execution_id: str | None = None
    context: dict[str, Any] | None = None
    current_task_id: str | None = None


class ExecutionDetail(ExecutionOut):
    agent_name: str
    events: list["EventOut"] = Field(default_factory=list)
    incidents: list["IncidentOut"] = Field(default_factory=list)
    interventions: list["InterventionOut"] = Field(default_factory=list)
    #: Latest model verdict on whether the output met the task, if one was run.
    output_evaluation: dict[str, Any] | None = None
    tasks: list["TaskOut"] = Field(default_factory=list)
    decisions: list["SupervisorDecisionOut"] = Field(default_factory=list)
    approvals: list["ApprovalOut"] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


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
    task_id: str | None = None
    actor: str | None = None


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
    task_id: str | None = None


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
    #: Send each completed run's task, trace digest and output summary to the
    #: supervisor LLM and raise a ``poor_output`` incident when it judges the
    #: objective unmet. Opt-in: it discloses run content to the provider.
    evaluate_output_on_completion: bool = False

    # --- Progress -----------------------------------------------------------
    #: A running task silent this long, with no tool call outstanding, raises
    #: a ``stalled`` incident. None disables it.
    stall_seconds: int | None = Field(default=600, ge=30)

    # --- Decision points (tool calls about to run) ------------------------
    #: Built-in risk rules (destructive shell commands, credential access,
    #: force-pushes...). See ``supervision/risk.py``.
    risk_rules_enabled: bool = True
    #: In control mode, what a risky operation gets.
    risk_action: Literal["request_approval", "block", "warn"] = "request_approval"
    #: In control mode, what the next identical call in a detected loop gets.
    loop_action: Literal["block", "warn"] = "block"
    #: Tools that always need a human yes (control mode).
    approval_required_tools: list[str] = Field(default_factory=list)
    #: Tools that are never allowed (control mode); a reason is returned.
    blocked_tools: list[str] = Field(default_factory=list)
    #: How long an adapter waits for a human before ``approval_timeout_action``.
    approval_timeout_seconds: int = Field(default=120, ge=5, le=540)
    #: "deny" refuses on timeout; "ask" hands the decision back to the
    #: platform's own permission prompt.
    approval_timeout_action: Literal["deny", "ask"] = "deny"

    # --- Completion review --------------------------------------------------
    #: Review the output before the agent is allowed to finish, and send it
    #: back with the gaps if the review fails (platforms with feedback only).
    review_output_before_completion: bool = False
    #: Most times one task can be sent back. Prevents correction loops.
    max_redirects: int = Field(default=1, ge=0, le=3)

    #: Route the platform's own permission prompts (Claude Code
    #: ``PermissionRequest``) to the dashboard approval queue. Off: the
    #: platform asks locally as usual (headless sessions deny).
    forward_permission_requests: bool = False


SupervisionModeIn = Literal["observe", "recommend", "control", "assist", "autopilot"]


class PolicyUpsert(BaseModel):
    agent_id: str | None = None
    name: str = "default"
    supervision_mode: SupervisionModeIn = "recommend"
    configuration: PolicyConfiguration = Field(default_factory=PolicyConfiguration)

    @field_validator("supervision_mode")
    @classmethod
    def _normalise_mode(cls, value: str) -> str:
        return {"assist": "recommend", "autopilot": "control"}.get(value, value)


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




# ---------------------------------------------------------------------------
# Universal event protocol
# ---------------------------------------------------------------------------

from app.events import EVENT_TYPES as PROTOCOL_EVENT_TYPES  # noqa: E402
from app.models import Platform as _Platform  # noqa: E402


class ExecutionContextIn(BaseModel):
    """Where in the agent environment the event happened."""

    model_config = ConfigDict(extra="allow")

    #: Sub-agent / handoff target that produced the event, if not the root.
    actor: str | None = None
    parent_session_id: str | None = None
    cwd: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    host: str | None = None


class ProtocolEventIn(BaseModel):
    """One event in the universal protocol. Field names follow the spec."""

    event_id: str = Field(min_length=1, max_length=64)
    platform: Literal["claude_code", "openai_agents", "custom_sdk"]
    #: Optional when the request authenticates with an integration-scoped key
    #: (the key determines it); required otherwise.
    integration_id: str | None = None
    #: The platform's identity for the agent.
    agent_id: str = Field(min_length=1, max_length=300)
    session_id: str = Field(min_length=1, max_length=200)
    event_type: str
    timestamp: datetime
    task_id: str | None = Field(default=None, max_length=200)
    execution_context: ExecutionContextIn = Field(default_factory=ExecutionContextIn)
    payload: dict[str, Any] = Field(default_factory=dict)
    #: Display name for the agent, used when it is first seen.
    agent_name: str | None = Field(default=None, max_length=200)

    @field_validator("event_type")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in PROTOCOL_EVENT_TYPES:
            raise ValueError(f"unknown event_type: {value!r}")
        return value


class ProtocolBatchIn(BaseModel):
    events: list[ProtocolEventIn] = Field(min_length=1, max_length=500)


class SessionControlOut(BaseModel):
    """Control state for one session, in a form any adapter can act on."""

    session_id: str
    execution_id: str
    desired_state: Literal["running", "paused", "cancelled"]
    blocked_tools: list[str] = Field(default_factory=list)
    commands: list[CommandOut] = Field(default_factory=list)


class ProtocolIngestOut(BaseModel):
    accepted: int
    duplicates: int
    sessions: list[SessionControlOut]


class DecisionRequestIn(BaseModel):
    """A decision point: the event that is about to happen."""

    event: ProtocolEventIn
    #: Claude Code's ``stop_hook_active``: the agent is already continuing
    #: because of an earlier block, so never redirect it again.
    already_redirected: bool = False


class ApprovalTicket(BaseModel):
    id: str
    expires_at: datetime
    on_timeout: Literal["deny", "ask"]
    wait_url: str


class DecisionOut(BaseModel):
    decision_id: str
    execution_id: str
    action: Literal[
        "ALLOW", "WARN", "REQUEST_APPROVAL", "BLOCK", "PAUSE", "RETRY",
        "REDIRECT", "ESCALATE_TO_HUMAN", "STOP",
    ]
    reason: str
    #: Text for the agent itself (deny reason, corrective feedback).
    feedback: str | None = None
    rule: str
    approval: ApprovalTicket | None = None
    #: Set when policy or platform capability forced a milder action.
    intended_action: str | None = None
    held_back_reason: str | None = None


class ApprovalOut(ORMModel):
    id: str
    execution_id: str
    agent_id: str
    task_id: str | None
    tool_name: str | None
    action_preview: dict[str, Any]
    reason: str
    status: str
    expires_at: datetime
    on_timeout: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    created_at: datetime


class ApprovalDecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------

class IntegrationCreate(BaseModel):
    platform: Literal["claude_code", "openai_agents", "custom_sdk"]
    name: str = Field(min_length=1, max_length=200)


class IntegrationConnectIn(BaseModel):
    """Sent by an adapter when it starts: what it is and what it can do."""

    capabilities: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class IntegrationOut(ORMModel):
    id: str
    platform: str
    name: str
    capabilities: list[str]
    details: dict[str, Any]
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class IntegrationCreated(IntegrationOut):
    #: Returned exactly once.
    key: str


class SupervisorDecisionOut(ORMModel):
    id: str
    execution_id: str
    agent_id: str
    task_id: str | None
    event_type: str
    tool_name: str | None
    action: str
    reason: str
    feedback: str | None
    rule: str
    mode: str
    intended_action: str | None
    held_back_reason: str | None
    approval_id: str | None
    incident_id: str | None
    latency_ms: float | None
    created_at: datetime


class TaskOut(ORMModel):
    id: str
    execution_id: str
    agent_id: str
    external_task_id: str | None
    objective: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    tool_call_count: int
    error_count: int
    model_calls: int
    output_summary: str | None
    evaluation: dict[str, Any] | None
    redirects: int


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=200)
    name: str | None = Field(default=None, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class SessionTokenOut(BaseModel):
    token: str
    expires_at: datetime
    user: dict[str, Any]


ExecutionDetail.model_rebuild()
