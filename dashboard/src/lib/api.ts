/**
 * Typed client for the Reliability backend.
 *
 * Types mirror `backend/app/schemas.py`. When that file changes, change this
 * one in the same commit - the contract is only useful if both ends agree.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_RELIABILITY_API_URL ?? "http://127.0.0.1:8000";

const TOKEN_KEY = "reliability.token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return (
    window.localStorage.getItem(TOKEN_KEY) ??
    process.env.NEXT_PUBLIC_RELIABILITY_TOKEN ??
    ""
  );
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  if (!token) throw new ApiError("No API token configured", 401);

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* -------------------------------------------------------------------------
 * Types
 * ---------------------------------------------------------------------- */

export type ExecutionStatus =
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";
export type AgentStatus = "idle" | "running" | "warning" | "failed" | "paused";
export type Severity = "critical" | "high" | "medium" | "low" | "informational";
export type SupervisionMode = "observe" | "assist" | "autopilot";

export interface Agent {
  id: string;
  name: string;
  framework: string;
  status: AgentStatus;
  description: string | null;
  sdk_version: string | null;
  created_at: string;
  last_seen_at: string | null;
  execution_count: number;
  success_count: number;
  failure_count: number;
  running_count: number;
  open_incident_count: number;
  success_rate: number | null;
  total_tokens: number;
  estimated_cost: number;
  last_activity_at: string | null;
}

export interface Execution {
  id: string;
  agent_id: string;
  status: ExecutionStatus;
  task: string | null;
  started_at: string;
  completed_at: string | null;
  /** Last event received from this runtime. */
  last_event_at: string | null;
  /** Last proof of life: an event, or a control poll while paused. */
  last_heartbeat_at: string | null;
  event_count: number;
  tool_call_count: number;
  error_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  token_usage: number;
  estimated_cost: number | null;
  cost_complete: boolean;
  error: string | null;
  output_summary: string | null;
}

export interface TraceEvent {
  id: string;
  seq: number;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface Incident {
  id: string;
  execution_id: string;
  agent_id: string;
  severity: Severity;
  category: string;
  title: string;
  description: string;
  evidence: Record<string, unknown>;
  source: string;
  diagnosis: string | null;
  diagnosis_source: string | null;
  recommended_action: string | null;
  action_taken: string | null;
  status: "open" | "acknowledged" | "resolved";
  created_at: string;
  updated_at: string;
  agent_name: string;
  execution_status: ExecutionStatus;
  execution_task: string | null;
}

export interface Intervention {
  id: string;
  execution_id: string;
  incident_id: string | null;
  action: string;
  status: "pending" | "acknowledged" | "applied" | "failed" | "expired";
  reason: string | null;
  requested_by: string;
  parameters: Record<string, unknown>;
  created_at: string;
  delivered_at: string | null;
  acknowledged_at: string | null;
  applied_at: string | null;
  runtime_result: Record<string, unknown> | null;
}

export interface ExecutionDetail extends Execution {
  agent_name: string;
  events: TraceEvent[];
  incidents: Incident[];
  interventions: Intervention[];
}

export interface Overview {
  total_agents: number;
  running_agents: number;
  running_executions: number;
  executions_total: number;
  executions_succeeded: number;
  executions_failed: number;
  executions_cancelled: number;
  open_incidents: number;
  total_tokens: number;
  estimated_cost: number;
  cost_complete: boolean;
}

export interface PolicyConfiguration {
  max_duration_seconds: number | null;
  max_tool_calls: number | null;
  max_tokens: number | null;
  max_cost_usd: number | null;
  repeated_tool_call_threshold: number | null;
  repeated_error_threshold: number | null;
  auto_retry_enabled: boolean;
  max_retries: number;
  permitted_interventions: string[];
  require_approval_for: string[];
}

export interface Policy {
  id: string;
  agent_id: string | null;
  name: string;
  supervision_mode: SupervisionMode;
  configuration: PolicyConfiguration;
  created_at: string;
  updated_at: string;
}

export interface RuntimeConfig {
  environment: string;
  event_bus: string;
  database: string;
  llm_evaluator: { configured: boolean; provider: string; model: string | null };
  user: { id: string; email: string };
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface ChatResponse {
  answer: string;
  grounded_in: Record<string, number>;
  evaluator: string | null;
  unavailable_reason: string | null;
}

/* -------------------------------------------------------------------------
 * Endpoints
 * ---------------------------------------------------------------------- */

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const api = {
  config: () => request<RuntimeConfig>("/v1/config"),
  overview: () => request<Overview>("/v1/metrics/overview"),

  agents: () => request<Agent[]>("/v1/agents"),
  agent: (id: string) => request<Agent>(`/v1/agents/${id}`),

  executions: (params: { agent_id?: string; status?: string; active?: boolean; limit?: number } = {}) =>
    request<Execution[]>(`/v1/executions${qs(params)}`),
  execution: (id: string) => request<ExecutionDetail>(`/v1/executions/${id}`),
  events: (id: string, afterSeq?: number) =>
    request<TraceEvent[]>(`/v1/executions/${id}/events${qs({ after_seq: afterSeq })}`),

  interventions: (executionId: string) =>
    request<Intervention[]>(`/v1/executions/${executionId}/interventions`),
  control: (executionId: string, action: string, reason?: string, parameters?: Record<string, unknown>) =>
    request<Intervention>(`/v1/executions/${executionId}/interventions`, {
      method: "POST",
      body: JSON.stringify({ action, reason, parameters: parameters ?? {} }),
    }),

  incidents: (params: { status?: string; severity?: string; agent_id?: string; execution_id?: string } = {}) =>
    request<Incident[]>(`/v1/incidents${qs(params)}`),
  incident: (id: string) => request<Incident>(`/v1/incidents/${id}`),
  updateIncident: (id: string, status: string) =>
    request<Incident>(`/v1/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  diagnose: (id: string) =>
    request<Incident>(`/v1/incidents/${id}/diagnose`, { method: "POST" }),

  policies: () => request<Policy[]>("/v1/policies"),
  policyDefaults: () =>
    request<{ supervision_mode: SupervisionMode; configuration: PolicyConfiguration }>(
      "/v1/policies/defaults",
    ),
  savePolicy: (body: {
    agent_id: string | null;
    name: string;
    supervision_mode: SupervisionMode;
    configuration: PolicyConfiguration;
  }) => request<Policy>("/v1/policies", { method: "PUT", body: JSON.stringify(body) }),
  deletePolicy: (id: string) => request<void>(`/v1/policies/${id}`, { method: "DELETE" }),

  keys: () => request<ApiKey[]>("/v1/keys"),
  createKey: (name: string) =>
    request<ApiKey & { key: string }>(`/v1/keys${qs({ name })}`, { method: "POST" }),
  revokeKey: (id: string) => request<void>(`/v1/keys/${id}`, { method: "DELETE" }),

  audit: () => request<AuditEntry[]>("/v1/audit"),

  chat: (message: string, scope: { agent_id?: string; execution_id?: string } = {}) =>
    request<ChatResponse>("/v1/supervisor/chat", {
      method: "POST",
      body: JSON.stringify({ message, ...scope }),
    }),
};
