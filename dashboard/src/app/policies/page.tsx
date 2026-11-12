"use client";

import { useEffect, useState } from "react";
import { api, type PolicyConfiguration, type SupervisionMode } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { fullNumber } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Loading,
  Mono,
  Select,
  cn,
} from "@/components/ui";

const MODES: Array<{ value: SupervisionMode; title: string; blurb: string }> = [
  {
    value: "observe",
    title: "Observe",
    blurb: "Record and report. No recommendations, no interventions.",
  },
  {
    value: "recommend",
    title: "Recommend",
    blurb: "Raise issues and tell the agent what is wrong. Nothing is refused; a human decides.",
  },
  {
    value: "control",
    title: "Control",
    blurb: "Block, hold for approval, stop or pause — only what the policy permits and the platform supports.",
  },
];

const ACTIONS = ["pause", "cancel", "block_tool"];
const CATEGORIES = [
  "repeated_tool_call",
  "execution_timeout",
  "tool_call_limit",
  "token_budget",
  "cost_budget",
  "repeated_errors",
];

/** `null` means "no limit"; an empty input is how the UI expresses that. */
function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export default function PoliciesPage() {
  const policies = useAsync(() => api.policies(), [], { intervalMs: 30000 });
  const agents = useAsync(() => api.agents(), []);
  const defaults = useAsync(() => api.policyDefaults(), []);

  const [scope, setScope] = useState<string>("");
  const [mode, setMode] = useState<SupervisionMode>("recommend");
  const [config, setConfig] = useState<PolicyConfiguration | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load whichever policy the selected scope resolves to, falling back to the
  // backend's built-in defaults so the form always shows what would apply.
  useEffect(() => {
    if (!defaults.data) return;
    const existing = policies.data?.find((row) => (row.agent_id ?? "") === scope);
    setMode(existing?.supervision_mode ?? defaults.data.supervision_mode);
    setConfig({ ...defaults.data.configuration, ...(existing?.configuration ?? {}) });
    setMessage(null);
    setError(null);
  }, [scope, policies.data, defaults.data]);

  const save = async () => {
    if (!config) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      await api.savePolicy({
        agent_id: scope || null,
        name: scope ? "agent-policy" : "workspace-default",
        supervision_mode: mode,
        configuration: config,
      });
      setMessage("Policy saved. It applies to the next evaluation pass.");
      policies.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the policy");
    } finally {
      setSaving(false);
    }
  };

  const update = (patch: Partial<PolicyConfiguration>) =>
    setConfig((current) => (current ? { ...current, ...patch } : current));

  const toggle = (key: "permitted_interventions" | "require_approval_for", value: string) =>
    setConfig((current) => {
      if (!current) return current;
      const list = current[key];
      return {
        ...current,
        [key]: list.includes(value)
          ? list.filter((item) => item !== value)
          : [...list, value],
      };
    });

  if (!config || defaults.loading) {
    return (
      <>
        <PageHeader title="Policies" />
        <Loading />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Policies"
        description="Execution limits and how much authority the supervisor has to act on them."
        actions={
          <Select value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="">Workspace default</option>
            {agents.data?.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </Select>
        }
      />
      <PageBody>
        <Card>
          <CardHeader
            title="Supervision mode"
            description="Limits are always enforced deterministically. The mode decides what happens next."
          />
          <div className="grid gap-3 p-4 md:grid-cols-3">
            {MODES.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setMode(option.value)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  mode === option.value
                    ? "border-[var(--accent)] bg-[var(--accent)]/10"
                    : "border-line bg-panel-raised hover:border-line-strong",
                )}
              >
                <div className="text-sm font-medium text-ink">{option.title}</div>
                <p className="mt-1 text-xs leading-relaxed text-muted">{option.blurb}</p>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Execution limits"
            description="Leave a field empty to disable that limit entirely."
          />
          <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Max duration (seconds)">
              <Input
                type="number"
                min={1}
                value={config.max_duration_seconds ?? ""}
                onChange={(event) =>
                  update({ max_duration_seconds: numberOrNull(event.target.value) })
                }
              />
            </Field>
            <Field label="Max tool calls">
              <Input
                type="number"
                min={1}
                value={config.max_tool_calls ?? ""}
                onChange={(event) =>
                  update({ max_tool_calls: numberOrNull(event.target.value) })
                }
              />
            </Field>
            <Field label="Max tokens">
              <Input
                type="number"
                min={1}
                value={config.max_tokens ?? ""}
                onChange={(event) => update({ max_tokens: numberOrNull(event.target.value) })}
              />
            </Field>
            <Field label="Max estimated cost (USD)">
              <Input
                type="number"
                min={0}
                step="0.01"
                value={config.max_cost_usd ?? ""}
                onChange={(event) =>
                  update({ max_cost_usd: numberOrNull(event.target.value) })
                }
              />
            </Field>
            <Field label="Repeated tool call threshold">
              <Input
                type="number"
                min={2}
                value={config.repeated_tool_call_threshold ?? ""}
                onChange={(event) =>
                  update({
                    repeated_tool_call_threshold: numberOrNull(event.target.value),
                  })
                }
              />
            </Field>
            <Field label="Repeated error threshold">
              <Input
                type="number"
                min={2}
                value={config.repeated_error_threshold ?? ""}
                onChange={(event) =>
                  update({ repeated_error_threshold: numberOrNull(event.target.value) })
                }
              />
            </Field>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Authorisation"
            description="Asynchronous actions control mode may take on platforms that support them (custom SDK, OpenAI Agents SDK), and categories that always go to a human."
          />
          <div className="space-y-4 p-4">
            <div>
              <div className="mb-2 text-xs text-muted">Permitted interventions</div>
              <div className="flex flex-wrap gap-2">
                {ACTIONS.map((action) => {
                  const on = config.permitted_interventions.includes(action);
                  return (
                    <button
                      key={action}
                      type="button"
                      onClick={() => toggle("permitted_interventions", action)}
                      className={cn(
                        "rounded-md border px-2.5 py-1 text-xs transition-colors",
                        on
                          ? "border-[var(--success)]/50 bg-[var(--success)]/10 text-[var(--success)]"
                          : "border-line-strong bg-panel-raised text-muted hover:text-ink",
                      )}
                    >
                      {action}
                    </button>
                  );
                })}
              </div>
              {mode === "control" && config.permitted_interventions.length === 0 ? (
                <p className="mt-2 text-[11px] text-[var(--warning)]">
                  Control mode is on but no asynchronous action is permitted, so it will only record
                  that it held back.
                </p>
              ) : null}
            </div>

            <div>
              <div className="mb-2 text-xs text-muted">
                Always require human approval for
              </div>
              <div className="flex flex-wrap gap-2">
                {CATEGORIES.map((category) => {
                  const on = config.require_approval_for.includes(category);
                  return (
                    <button
                      key={category}
                      type="button"
                      onClick={() => toggle("require_approval_for", category)}
                      className={cn(
                        "rounded-md border px-2.5 py-1 text-xs transition-colors",
                        on
                          ? "border-[var(--warning)]/50 bg-[var(--warning)]/10 text-[var(--warning)]"
                          : "border-line-strong bg-panel-raised text-muted hover:text-ink",
                      )}
                    >
                      {category}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Decision points"
            description="What the supervisor says when an agent is about to run a tool or finish a task. Recommend mode turns every refusal into a warning the agent sees."
          />
          <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Risky operations (control mode)">
              <Select
                value={config.risk_rules_enabled ? config.risk_action : "off"}
                onChange={(event) =>
                  event.target.value === "off"
                    ? update({ risk_rules_enabled: false })
                    : update({
                        risk_rules_enabled: true,
                        risk_action: event.target.value as PolicyConfiguration["risk_action"],
                      })
                }
              >
                <option value="request_approval">Hold for human approval</option>
                <option value="block">Refuse with a reason</option>
                <option value="warn">Warn only</option>
                <option value="off">Risk rules off</option>
              </Select>
            </Field>
            <Field label="Repeating a call that keeps returning the same thing">
              <Select
                value={config.loop_action}
                onChange={(event) =>
                  update({ loop_action: event.target.value as PolicyConfiguration["loop_action"] })
                }
              >
                <option value="block">Refuse it, with corrective feedback</option>
                <option value="warn">Warn only</option>
              </Select>
            </Field>
            <Field label="Approval timeout">
              <div className="flex gap-2">
                <Input
                  type="number"
                  min={5}
                  max={540}
                  value={config.approval_timeout_seconds}
                  onChange={(event) =>
                    update({ approval_timeout_seconds: Number(event.target.value) || 120 })
                  }
                />
                <Select
                  value={config.approval_timeout_action}
                  onChange={(event) =>
                    update({
                      approval_timeout_action: event.target.value as PolicyConfiguration["approval_timeout_action"],
                    })
                  }
                >
                  <option value="deny">then deny</option>
                  <option value="ask">then ask locally</option>
                </Select>
              </div>
            </Field>
            <Field label="Always need approval (tool names, comma separated)">
              <Input
                value={config.approval_required_tools.join(", ")}
                placeholder="e.g. Bash, issue_refund"
                onChange={(event) =>
                  update({
                    approval_required_tools: event.target.value
                      .split(",")
                      .map((t) => t.trim())
                      .filter(Boolean),
                  })
                }
              />
            </Field>
            <Field label="Never allowed (tool names)">
              <Input
                value={config.blocked_tools.join(", ")}
                placeholder="e.g. WebFetch"
                onChange={(event) =>
                  update({
                    blocked_tools: event.target.value
                      .split(",")
                      .map((t) => t.trim())
                      .filter(Boolean),
                  })
                }
              />
            </Field>
            <Field label="Stall alert after (seconds of silence)">
              <Input
                type="number"
                min={30}
                value={config.stall_seconds ?? ""}
                onChange={(event) => update({ stall_seconds: numberOrNull(event.target.value) })}
              />
            </Field>
          </div>
          <div className="space-y-3 border-t border-line p-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={config.review_output_before_completion}
                onChange={(event) => update({ review_output_before_completion: event.target.checked })}
              />
              <span className="text-xs leading-relaxed text-muted">
                <span className="text-ink">Review the answer before the agent may finish.</span> If it
                misses the task, the agent is sent back with the gaps — at most{" "}
                <input
                  type="number"
                  min={0}
                  max={3}
                  value={config.max_redirects}
                  onChange={(event) => update({ max_redirects: Number(event.target.value) || 0 })}
                  className="w-10 rounded border border-line bg-bg px-1 text-center text-ink"
                />{" "}
                time(s) per task, so correction can never loop. Platforms with corrective feedback
                only.
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={config.forward_permission_requests}
                onChange={(event) => update({ forward_permission_requests: event.target.checked })}
              />
              <span className="text-xs leading-relaxed text-muted">
                <span className="text-ink">Answer Claude Code&apos;s own permission prompts here.</span>{" "}
                Prompts go to the Approvals queue instead of the terminal — useful for headless and
                remote sessions, which otherwise deny them.
              </span>
            </label>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Output evaluation"
            description="Deterministic rules cannot tell whether an answer is right. This asks the supervisor LLM."
          />
          <label className="flex cursor-pointer items-start gap-3 p-4">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={config.evaluate_output_on_completion}
              onChange={(event) =>
                update({ evaluate_output_on_completion: event.target.checked })
              }
            />
            <span className="text-xs leading-relaxed text-muted">
              <span className="text-ink">Evaluate every completed run.</span> Sends the
              task, trace digest and output summary to the configured supervisor model
              and raises a <code>poor_output</code> incident when it judges the
              objective unmet with medium or high confidence. Each run costs one
              model call.
            </span>
          </label>
        </Card>

        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : scope ? "Save agent policy" : "Save workspace default"}
          </Button>
          {message ? (
            <span className="text-xs text-[var(--success)]">{message}</span>
          ) : null}
          {error ? <span className="text-xs text-[var(--failed)]">{error}</span> : null}
        </div>

        <Card>
          <CardHeader title="Saved policies" />
          {!policies.data?.length ? (
            <EmptyState
              title="No policies saved"
              description="Built-in defaults apply until you save one."
            />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {policies.data.map((policy) => {
                const agent = agents.data?.find((row) => row.id === policy.agent_id);
                return (
                  <li
                    key={policy.id}
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-ink">
                          {policy.agent_id ? (agent?.name ?? policy.agent_id) : "Workspace default"}
                        </span>
                        <Badge>{policy.supervision_mode}</Badge>
                      </div>
                      <Mono className="mt-0.5 block">
                        {policy.configuration.max_tool_calls ?? "∞"} tool calls ·{" "}
                        {policy.configuration.max_tokens
                          ? fullNumber(policy.configuration.max_tokens)
                          : "∞"}{" "}
                        tokens · repeat ≥{" "}
                        {policy.configuration.repeated_tool_call_threshold ?? "off"}
                      </Mono>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={async () => {
                        await api.deletePolicy(policy.id);
                        policies.refresh();
                      }}
                    >
                      Delete
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </PageBody>
    </>
  );
}
