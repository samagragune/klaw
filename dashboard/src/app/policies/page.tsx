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
    value: "assist",
    title: "Assist",
    blurb: "Raise incidents with a recommended action. A human decides.",
  },
  {
    value: "autopilot",
    title: "Autopilot",
    blurb: "Act automatically, but only the actions permitted below.",
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
  const [mode, setMode] = useState<SupervisionMode>("assist");
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
            description="Autopilot may only take an action listed here, and never for a category that requires approval."
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
              {mode === "autopilot" && config.permitted_interventions.length === 0 ? (
                <p className="mt-2 text-[11px] text-[var(--warning)]">
                  Autopilot is on but no action is permitted, so it will only record
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
