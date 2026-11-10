"""SQLAlchemy models.

Design notes
------------
* Every tenant-owned row carries ``user_id`` directly, even when it could be
  derived through a join. That makes user isolation a single predicate on
  every query instead of a join that is easy to forget.
* Payload-ish columns use JSON so the same schema works on SQLite and
  Postgres without dialect-specific types.
* Timestamps are stored timezone-aware in UTC.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC in Python.

    SQLite has no timezone type and hands back naive datetimes, which would
    otherwise be serialised without an offset - and a browser reads a bare
    timestamp as local time, putting every event hours out. Normalising here
    fixes the API, the detectors and the dashboard in one place.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# Status constants (plain strings, for schema portability)
# --------------------------------------------------------------------------

class AgentStatus:
    IDLE = "idle"
    RUNNING = "running"
    WARNING = "warning"
    FAILED = "failed"
    PAUSED = "paused"


class ExecutionStatus:
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = {COMPLETED, FAILED, CANCELLED}


class IncidentSeverity:
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "informational"


class IncidentStatus:
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class InterventionStatus:
    """Lifecycle of a control command.

    PENDING      - stored, awaiting delivery to the runtime
    ACKNOWLEDGED - the SDK received it but has not reached a checkpoint yet
    APPLIED      - the runtime confirmed the action actually took effect
    FAILED       - the runtime reported it could not apply the action
    EXPIRED      - execution finished before the command could be applied
    """

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"


class InterventionAction:
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    BLOCK_TOOL = "block_tool"
    REQUEST_APPROVAL = "request_approval"


class SupervisionMode:
    OBSERVE = "observe"
    ASSIST = "assist"
    AUTOPILOT = "autopilot"


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)

    agents: Mapped[list["Agent"]] = relationship(back_populates="user")


class ApiKey(Base, TimestampMixin):
    """An SDK credential.

    Only the SHA-256 hash is stored. ``prefix`` is a non-secret display
    fragment so a key can be recognised in the UI without being recoverable.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_agent_user_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    framework: Mapped[str] = mapped_column(String(80), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AgentStatus.IDLE)
    description: Mapped[str | None] = mapped_column(Text)
    sdk_version: Mapped[str | None] = mapped_column(String(40))
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    user: Mapped[User] = relationship(back_populates="agents")
    executions: Mapped[list["Execution"]] = relationship(back_populates="agent")


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_agent_started", "agent_id", "started_at"),
        Index("ix_executions_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ExecutionStatus.RUNNING)
    task: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Liveness. An agent proves it is alive two ways: by sending events, and
    # by polling for control commands - the second keeps working while the
    # execution is paused and emitting nothing.
    last_event_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    # Counters maintained incrementally as events arrive, so dashboard metrics
    # never require a scan over the event table.
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_usage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    # False once any LLM call used a model with no known price, so the UI
    # can label the total as partial instead of implying it is exact.
    cost_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    error: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSON)

    agent: Mapped[Agent] = relationship(back_populates="executions")
    events: Mapped[list["ExecutionEvent"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        Index("ix_events_execution_seq", "execution_id", "seq"),
        UniqueConstraint("execution_id", "client_event_id", name="uq_event_client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Monotonic per-execution sequence assigned by the SDK, so the backend can
    # order events that arrive batched or slightly out of order.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # SDK-generated id that makes ingestion idempotent across retries.
    client_event_id: Mapped[str] = mapped_column(String(64), nullable=False)

    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )

    execution: Mapped[Execution] = relationship(back_populates="events")


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_user_status", "user_id", "status"),
        UniqueConstraint("execution_id", "category", name="uq_incident_execution_category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Evidence is always drawn from stored events, never from generated text.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="deterministic")

    diagnosis: Mapped[str | None] = mapped_column(Text)
    diagnosis_source: Mapped[str | None] = mapped_column(String(40))
    recommended_action: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=IncidentStatus.OPEN)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Policy(Base, TimestampMixin):
    """Execution limits for one agent, or the user-wide default when
    ``agent_id`` is NULL."""

    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("user_id", "agent_id", name="uq_policy_user_agent"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    supervision_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SupervisionMode.ASSIST
    )
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Intervention(Base, TimestampMixin):
    __tablename__ = "interventions"
    __table_args__ = (Index("ix_interventions_exec_status", "execution_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)

    action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InterventionStatus.PENDING
    )
    reason: Mapped[str | None] = mapped_column(Text)
    # "user" (dashboard/API) or "supervisor" (autopilot policy enforcement)
    requested_by: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Verbatim runtime confirmation. Until this is populated, nothing in the
    # product may claim the intervention took effect.
    runtime_result: Mapped[dict | None] = mapped_column(JSON)


class AuditLog(Base, TimestampMixin):
    """Append-only record of every control action and sensitive change."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
