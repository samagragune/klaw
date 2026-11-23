/**
 * Typed client for the Klaw backend.
 *
 * Types mirror `backend/app/schemas.py`. When that file changes, change this
 * one in the same commit - the contract is only useful if both ends agree.
 */

const DEFAULT_API_URL =
  process.env.NEXT_PUBLIC_KLAW_API_URL ?? "http://127.0.0.1:8000";
const API_URL_KEY = "klaw.api_url";

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

const TOKEN_KEY = "klaw.token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try {
    // NEXT_PUBLIC_KLAW_TOKEN is a local-development convenience only:
    // anything NEXT_PUBLIC_ is readable by whoever loads the page, so a
    // deployed dashboard must never set it. Deployments sign in instead.
    return (
      window.localStorage.getItem(TOKEN_KEY) ??
      process.env.NEXT_PUBLIC_KLAW_TOKEN ??
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

/* ---------------------------------------------------------------- hosting
 * The console stays usable with no supervisor (and so no model) behind it:
 * "preview" mode, or a supervisor that cannot be reached. Every place that
 * would show a value then says so, instead of the page breaking.
 */

export const NOT_HOSTED = "Model not hosted yet";
const PREVIEW_KEY = "klaw.preview";

export function isPreview(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PREVIEW_KEY) === "1" && !window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return false;
  }
}

export function enterPreview() {
  window.localStorage.setItem(PREVIEW_KEY, "1");
}

export function exitPreview() {
  window.localStorage.removeItem(PREVIEW_KEY);
}

let hosted = true;
const hostedListeners = new Set<() => void>();

function setHosted(value: boolean) {
  if (hosted === value) return;
  hosted = value;
  hostedListeners.forEach((listener) => listener());
}

export const hostedStore = {
  get: () => hosted && !isPreview(),
  subscribe: (listener: () => void) => {
    hostedListeners.add(listener);
    return () => hostedListeners.delete(listener);
  },
};

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
    if (isPreview()) {
      setHosted(false);
      throw new ApiError(NOT_HOSTED, 0);
    }
    redirectToLogin();
    throw new ApiError("Not signed in", 401);
  }

  let response: Response;
  try {
    response = await fetch(`${getApiUrl()}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(init.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    // Nothing answered: the supervisor (and its model) is not running.
    setHosted(false);
    throw new ApiError(NOT_HOSTED, 0);
  }
  setHosted(true);

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
  agent_enabled?: boolean;
  agent_review_completion?: boolean;
  agent_max_attempts?: number;
  agent_monitor?: boolean;
  agent_plan_tasks?: boolean;
  agent_auto_reports?: boolean;
  agent_rules?: string[];
  agent_checks?: Record<string, string>;
  notify_webhook_url?: string | null;
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
  google: (credential: string) =>
    publicRequest<{ token: string; user: SessionUser }>("/v1/auth/google", { credential }),
  providers: async (): Promise<{ email: boolean; signup: boolean; google_client_ids: string[] }> => {
    const response = await fetch(`${getApiUrl()}/v1/auth/providers`, { cache: "no-store" });
    if (!response.ok) throw new ApiError("Supervisor did not answer", response.status);
    return response.json();
  },
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

  agentStatus: () => request<KlawAgentStatus>("/v1/agent"),
  agentFeed: (limit = 60) => request<AgentFeedItem[]>(`/v1/agent/feed${qs({ limit })}`),
  agentSession: (id: string) => request<AgentSessionView>(`/v1/agent/sessions/${id}`),
  agentChat: (message: string, execution_id?: string, history: { role: string; text: string }[] = []) =>
    request<AgentChatReply>("/v1/agent/chat", {
      method: "POST",
      body: JSON.stringify({ message, execution_id, history }),
    }),
  agentReview: (id: string, focus = "") =>
    request<AgentReview>(`/v1/agent/sessions/${id}/review`, { method: "POST", body: JSON.stringify({ focus }) }),
  agentReport: (id: string) => request<AgentReport>(`/v1/agent/sessions/${id}/report`, { method: "POST" }),
  agentReports: () => request<(AgentReport & { execution_id: string; task: string | null })[]>("/v1/agent/reports"),
  agentCheckpoints: (id: string) =>
    request<{ checkpoints: Checkpoint[]; changes_since_latest: string | null }>(`/v1/agent/sessions/${id}/checkpoints`),
  agentRestore: (id: string, sha: string) =>
    request<{ restored_to: string; undo_checkpoint: string | null; untracked_left_in_place: string[] }>(
      `/v1/agent/sessions/${id}/restore`,
      { method: "POST", body: JSON.stringify({ sha }) },
    ),
  agentDigest: (hours = 24) => request<AgentDigest>(`/v1/agent/digest${qs({ hours })}`),
  agentEffectiveness: () => request<AgentEffectiveness>("/v1/agent/effectiveness"),
  agentMemory: () => request<AgentMemoryItem[]>("/v1/agent/memory"),
  agentForget: (id: string) => request<void>(`/v1/agent/memory/${id}`, { method: "DELETE" }),

  chat: (message: string, scope: { agent_id?: string; execution_id?: string } = {}) =>
    request<ChatResponse>("/v1/supervisor/chat", {
      method: "POST",
      body: JSON.stringify({ message, ...scope }),
    }),
};

/* ---------------------------------------------------------------- the Klaw agent */

export interface KlawAgentStatus {
  available: boolean;
  enabled: boolean;
  provider: string;
  model: string;
  fast_model: string;
  reason: string | null;
  mode: SupervisionMode;
  cooling_down_seconds: number;
  last_error: string | null;
  live_sessions: number;
  last_24h: {
    actions_reviewed: number;
    allowed: number;
    guided: number;
    blocked: number;
    held_for_human: number;
    stopped: number;
    completions_passed: number;
    completions_incomplete: number;
    completions_retry: number;
    completions_escalated: number;
    model_calls: { purpose: string; calls: number; avg_latency_ms: number; cost_usd: number; ok: number }[];
  };
}

export interface AgentFeedItem {
  kind: "action" | "completion" | "guidance" | "assessment";
  at: string;
  execution_id: string;
  session_task: string | null;
  platform: Platform;
  tool_name?: string | null;
  action: string;
  verdict?: string | null;
  reasoning: string | null;
  guidance?: string | null;
  applied?: string | null;
  held_back?: string | null;
}

export interface AgentStep {
  tool: string;
  args: Record<string, unknown>;
  result: string;
}

export interface AgentInvestigation {
  at: string;
  purpose: string;
  steps: AgentStep[];
  seconds: number;
  model_calls: number;
  finished: boolean;
  error: string | null;
  conclusion: Record<string, unknown>;
}

export interface AgentPlan {
  requirements: string[];
  acceptance_checks: string[];
  risks: string[];
  progress: string;
  at: string;
}

export interface AgentSessionView {
  memory: string[];
  assessments: { at: string; assessment: string; action: string; applied: string; reasoning: string; guidance: string | null }[];
  last_assessment: AgentSessionView["assessments"][number] | null;
  inbox: { text: string; source: string; at: string }[];
  plans: Record<string, AgentPlan>;
  investigations: AgentInvestigation[];
}

export interface AgentChatReply {
  answer: string;
  steps: Omit<AgentStep, "result">[] & { result?: string }[];
  effects: { type: string; message?: string; reason?: string }[];
  execution_id?: string;
}

export interface AgentReview {
  at: string;
  summary: string;
  findings: { severity: string; file?: string; line?: number; issue: string; suggestion?: string }[];
  steps: AgentStep[];
}

export interface AgentReport {
  at: string;
  summary: string;
  done?: string[] | null;
  not_done?: string[] | null;
  files_changed?: string[] | null;
  risks?: string[] | null;
  follow_ups?: string[] | null;
}

export interface Checkpoint {
  sha: string;
  label: string;
  at: string;
}

export interface AgentEffectiveness {
  tasks_sent_back: number;
  sent_back_then_passed: number;
  send_back_success_rate: number | null;
  blocks: number;
  blocks_retried_anyway: number;
  block_acceptance_rate: number | null;
}

export interface AgentDigest {
  hours: number;
  sessions: number;
  by_platform: Record<string, number>;
  tasks: number;
  task_status: Record<string, number>;
  verdicts: Record<string, number>;
  decisions: Record<string, number>;
  incidents: { title: string; severity: Severity; category: string; execution_id: string }[];
  agent_cost: { model_calls: number; usd: number; tokens: number };
  effectiveness: AgentEffectiveness;
}

export interface AgentMemoryItem {
  id: string;
  project: string;
  note: string;
  at: string;
}
