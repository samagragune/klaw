/**
 * Typed client for the Reliability backend.
 *
 * Types mirror `backend/app/schemas.py`. When that file changes, change this
 * one in the same commit - the contract is only useful if both ends agree.
 */

const DEFAULT_API_URL =
  process.env.NEXT_PUBLIC_RELIABILITY_API_URL ?? "http://127.0.0.1:8000";
const API_URL_KEY = "reliability.api_url";

/**
 * The supervisor backend this browser talks to. Defaults to the build-time
 * value; can be changed on the sign-in page (for example to a backend running
 * on your own machine behind a tunnel) without rebuilding the dashboard.
 * Deliberately not settable from a URL parameter: a crafted link could
 * otherwise send someone's password to a server they did not choose.
 */
export function getApiUrl(): string {
  if (typeof window === "undefined") return DEFAULT_API_URL;
  try {
    return window.localStorage.getItem(API_URL_KEY) || DEFAULT_API_URL;
  } catch {
    return DEFAULT_API_URL;
  }
}

export function setApiUrl(url: string) {
  const clean = url.trim().replace(/\/+$/, "");
  if (!clean || clean === DEFAULT_API_URL) window.localStorage.removeItem(API_URL_KEY);
  else window.localStorage.setItem(API_URL_KEY, clean);
}

const TOKEN_KEY = "reliability.token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try {
    // NEXT_PUBLIC_RELIABILITY_TOKEN is a local-development convenience only:
    // anything NEXT_PUBLIC_ is readable by whoever loads the page, so a
    // deployed dashboard must never set it. Deployments sign in instead.
    return (
      window.localStorage.getItem(TOKEN_KEY) ??
      process.env.NEXT_PUBLIC_RELIABILITY_TOKEN ??
      ""
    );
  } catch {
    return "";
  }
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

function redirectToLogin() {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
}

/** Unauthenticated call (sign-in, sign-up). */
async function publicRequest<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getApiUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Request failed";
    throw new ApiError(detail, response.status);
  }
  return data as T;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  if (!token) {
    redirectToLogin();
    throw new ApiError("Not signed in", 401);
  }

  const response = await fetch(`${getApiUrl()}${path}`, {
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
    if (response.status === 401) {
      clearToken();
      redirectToLogin();
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
  | "idle"
  | "inactive"
  | "completed"
  | "failed"
  | "cancelled";
export type Platform = "claude_code" | "openai_agents" | "custom_sdk";
export type AgentStatus = "idle" | "running" | "warning" | "failed" | "paused";
export type Severity = "critical" | "high" | "medium" | "low" | "informational";
export type SupervisionMode = "observe" | "recommend" | "control";

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
  platform: Platform;
  integration_id: string | null;
  external_id: string | null;
  reliability_score: number | null;
  reliability: Reliability | null;
}

export interface Reliability {
  score: number | null;
  components: { task_success: number | null; clean_rate: number | null; tool_success: number | null };
  finished_tasks: number;
  running_tasks: number;
  reason: string | null;
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
  platform: Platform;
  integration_id: string | null;
  external_session_id: string | null;
  parent_execution_id: string | null;
  context: Record<string, unknown> | null;
  current_task_id: string | null;
}

export interface TraceEvent {
  id: string;
  seq: number;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
  task_id?: string | null;
  actor?: string | null;
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
  output_evaluation: OutputEvaluation | null;
  tasks: Task[];
  decisions: Decision[];
  approvals: Approval[];
  capabilities: string[];
}

export interface Task {
  id: string;
  execution_id: string;
  agent_id: string;
  external_task_id: string | null;
  objective: string | null;
  status: "running" | "completed" | "failed" | "cancelled" | "abandoned";
  started_at: string;
  completed_at: string | null;
  tool_call_count: number;
  error_count: number;
  model_calls: number;
  output_summary: string | null;
  evaluation: OutputEvaluation | null;
  redirects: number;
}

export interface Decision {
  id: string;
  execution_id: string;
  agent_id: string;
  task_id: string | null;
  event_type: string;
  tool_name: string | null;
  action: string;
  reason: string;
  feedback: string | null;
  rule: string;
  mode: string;
  intended_action: string | null;
  held_back_reason: string | null;
  approval_id: string | null;
  incident_id: string | null;
  latency_ms: number | null;
  created_at: string;
}

export interface Approval {
  id: string;
  execution_id: string;
  agent_id: string;
  task_id: string | null;
  tool_name: string | null;
  action_preview: Record<string, unknown>;
  reason: string;
  status: "pending" | "approved" | "denied" | "expired";
  expires_at: string;
  on_timeout: string;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  created_at: string;
}

export interface Integration {
  id: string;
  platform: Platform;
  name: string;
  capabilities: string[];
  details: Record<string, unknown>;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string;
  label: string;
  limitations: string[];
  agents: number;
  live_sessions: number;
  connection: "active" | "quiet" | "never_connected" | "revoked";
}

export interface PlatformInfo {
  platform: Platform;
  label: string;
  capabilities: string[];
  limitations: string[];
}

export interface PlatformOverview {
  platform: Platform;
  label: string;
  integrations: number;
  agents: number;
  live_sessions: number;
  sessions: number;
  open_incidents: number;
}

export interface ReliabilityReport {
  formula: string;
  weights: Record<string, number>;
  agents: Array<Reliability & { agent_id: string; name: string | null; platform: Platform | null }>;
}

export interface EvaluationCosts {
  days: number;
  calls: number;
  cached: number;
  tokens: number;
  estimated_cost: number;
  cost_complete: boolean;
  breakdown: Array<{
    purpose: string;
    model: string;
    calls: number;
    cached: number;
    failed: number;
    prompt_tokens: number;
    completion_tokens: number;
    estimated_cost: number | null;
    unpriced_calls: number;
    avg_latency_ms: number | null;
  }>;
}

export interface SupervisionSummary {
  decisions: Record<string, number>;
  pending_approvals: number;
  tasks: Record<string, number>;
}

export interface ActivityEvent extends TraceEvent {
  execution_id: string;
  platform: Platform;
  agent_id: string;
  agent_name: string;
}

export interface RiskRule {
  id: string;
  category: string;
  severity: string;
  description: string;
  applies_to: string;
}

export interface SessionUser {
  id: string;
  email: string;
  name: string | null;
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
  evaluate_output_on_completion: boolean;
  stall_seconds: number | null;
  risk_rules_enabled: boolean;
  risk_action: "request_approval" | "block" | "warn";
  loop_action: "block" | "warn";
  approval_required_tools: string[];
  blocked_tools: string[];
  approval_timeout_seconds: number;
  approval_timeout_action: "deny" | "ask";
  review_output_before_completion: boolean;
  max_redirects: number;
  forward_permission_requests: boolean;
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

export interface OutputEvaluation {
  satisfies_objective?: boolean | null;
  gaps?: string[];
  reasoning?: string;
  confidence?: string;
  evaluator?: string;
  evaluated_at?: string;
}

export const PLATFORM_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  openai_agents: "OpenAI Agents SDK",
  custom_sdk: "Custom SDK",
};

export const auth = {
  login: (email: string, password: string) =>
    publicRequest<{ token: string; user: SessionUser }>("/v1/auth/login", { email, password }),
  signup: (email: string, password: string, name?: string) =>
    publicRequest<{ token: string; user: SessionUser }>("/v1/auth/signup", { email, password, name }),
};

export const api = {
  me: () => request<SessionUser>("/v1/auth/me"),
  platforms: () => request<PlatformInfo[]>("/v1/integrations/platforms"),
  integrations: () => request<Integration[]>("/v1/integrations"),
  createIntegration: (platform: Platform, name: string) =>
    request<Integration & { key: string }>("/v1/integrations", {
      method: "POST",
      body: JSON.stringify({ platform, name }),
    }),
  revokeIntegration: (id: string) =>
    request<void>(`/v1/integrations/${id}`, { method: "DELETE" }),
  platformOverview: () => request<PlatformOverview[]>("/v1/metrics/platforms"),
  reliability: () => request<ReliabilityReport>("/v1/metrics/reliability"),
  evaluationCosts: (days = 30) =>
    request<EvaluationCosts>(`/v1/metrics/evaluation-costs${qs({ days })}`),
  supervision: () => request<SupervisionSummary>("/v1/metrics/supervision"),
  activity: (limit = 100) => request<ActivityEvent[]>(`/v1/activity${qs({ limit })}`),
  tasks: (params: { execution_id?: string; agent_id?: string; status?: string; limit?: number } = {}) =>
    request<Task[]>(`/v1/tasks${qs(params)}`),
  decisions: (params: { execution_id?: string; include_allow?: boolean; limit?: number } = {}) =>
    request<Decision[]>(`/v1/decisions${qs(params)}`),
  approvals: (params: { status?: string; execution_id?: string } = {}) =>
    request<Approval[]>(`/v1/approvals${qs(params)}`),
  decideApproval: (id: string, approve: boolean, note?: string) =>
    request<Approval>(`/v1/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ approve, note }),
    }),
  riskRules: () => request<RiskRule[]>("/v1/risk-rules"),
  config: () => request<RuntimeConfig>("/v1/config"),
  overview: () => request<Overview>("/v1/metrics/overview"),

  agents: () => request<Agent[]>("/v1/agents"),
  agent: (id: string) => request<Agent>(`/v1/agents/${id}`),

  executions: (params: { agent_id?: string; status?: string; active?: boolean; platform?: string; limit?: number } = {}) =>
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
  evaluateOutput: (executionId: string) =>
    request<{ verdict: OutputEvaluation; incident_id: string | null }>(
      `/v1/executions/${executionId}/evaluate-output`,
      { method: "POST" },
    ),
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
