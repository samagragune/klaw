"use client";

import { useState } from "react";
import { getApiUrl, api, type Integration, type Platform } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { PlatformBadge, Pre } from "@/components/platform";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Input, Loading, Select } from "@/components/ui";

/** Where the companion and SDK are installed from (override per deployment). */
const REPO = process.env.NEXT_PUBLIC_RELIABILITY_REPO ?? "https://github.com/samagragune/klaw.git";

function setupSteps(platform: Platform, key: string): { title: string; body: string }[] {
  if (platform === "claude_code") {
    return [
      {
        title: "Run this in the project you want supervised (Python 3.9+):",
        body:
          `pip install "git+${REPO}#subdirectory=companion" && ` +
          `reliability-companion setup --api-url ${getApiUrl()} --key ${key}`,
      },
      {
        title: "That's it. It shows the change to .claude/settings.json and asks before writing. Options:",
        body:
          "--user          supervise every project instead of the current one\n" +
          "--yes           skip the confirmation (scripts)\n" +
          "reliability-companion claude-code uninstall --project .   # remove it again",
      },
    ];
  }
  if (platform === "openai_agents") {
    return [
      {
        title: "1. Install into your app's environment",
        body: `pip install "git+${REPO}#subdirectory=sdk" openai-agents`,
      },
      {
        title: "2. Set two environment variables",
        body: `RELIABILITY_API_URL=${getApiUrl()}\nRELIABILITY_INTEGRATION_KEY=${key}`,
      },
      {
        title: "3. Change one line: Runner.run → supervisor.run",
        body: `from reliability.integrations.openai_agents import AgentsSupervisor

supervisor = AgentsSupervisor(app_name="my-app")
result = await supervisor.run(triage_agent, user_input)`,
      },
    ];
  }
  return [
    { title: "1. Install", body: `pip install "git+${REPO}#subdirectory=sdk"` },
    {
      title: "2. Use the key",
      body: `from reliability import Supervisor\nsupervisor = Supervisor(api_key="${key}", agent_id="my-agent", api_url="${getApiUrl()}")`,
    },
  ];
}

/** Watches a new integration until it checks in and sends its first session. */
function ConnectionWatcher({ id }: { id: string }) {
  const state = useAsync(() => api.integrations(), [id], { intervalMs: 3000 });
  const me = state.data?.find((i) => i.id === id);
  const step = !me || me.connection === "never_connected" ? 0 : me.agents === 0 ? 1 : 2;
  const labels = [
    "Waiting for the companion or SDK to connect…",
    "Connected. Waiting for the first agent session…",
    `Receiving data from ${me?.agents ?? 0} agent${me?.agents === 1 ? "" : "s"}.`,
  ];
  return (
    <div
      className="flex items-center gap-2 rounded border border-line px-3 py-2 text-xs"
      style={{ color: step === 2 ? "var(--success)" : "var(--warning)" }}
    >
      <span className={step < 2 ? "pulse size-1.5 rounded-full bg-current" : "size-1.5 rounded-full bg-current"} />
      {labels[step]}
      {step === 2 ? (
        <a href="/monitor" className="ml-auto text-ink underline">
          open sessions
        </a>
      ) : null}
    </div>
  );
}

function CreatedPanel({ created, onClose }: { created: Integration & { key: string }; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Card className="border-[var(--success)]/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-ink">“{created.name}” is ready</div>
          <p className="mt-1 text-xs text-muted">
            This key is shown once. It can only send events and request decisions for this
            integration; it cannot read the dashboard or change policy.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Done
        </Button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded border border-line bg-bg px-2 py-1.5 font-mono text-xs text-ink">
          {created.key}
        </code>
        <Button
          size="sm"
          onClick={() => {
            navigator.clipboard.writeText(created.key).then(() => setCopied(true));
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div className="mt-4 space-y-3">
        <ConnectionWatcher id={created.id} />
        {setupSteps(created.platform, created.key).map((step) => (
          <div key={step.title}>
            <div className="mb-1 text-xs font-medium text-ink">{step.title}</div>
            <Pre>{step.body}</Pre>
          </div>
        ))}
      </div>
    </Card>
  );
}

const CONNECTION: Record<string, { color: string; label: string }> = {
  active: { color: "var(--success)", label: "active" },
  quiet: { color: "var(--warning)", label: "quiet" },
  never_connected: { color: "var(--idle)", label: "not connected yet" },
  revoked: { color: "var(--failed)", label: "revoked" },
};

export default function IntegrationsPage() {
  const integrations = useAsync(() => api.integrations(), [], { intervalMs: 10000 });
  const platforms = useAsync(() => api.platforms(), []);
  const [platform, setPlatform] = useState<Platform>("claude_code");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<(Integration & { key: string }) | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.createIntegration(platform, name || `${platform}-${new Date().toISOString().slice(0, 10)}`);
      setCreated(result);
      setName("");
      integrations.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  const info = platforms.data?.find((p) => p.platform === platform);

  return (
    <>
      <PageHeader
        title="Integrations"
        description="Connect the agent environments you already use. Each integration declares what it can do; the supervisor never asks a platform for an action it cannot perform."
      />
      <PageBody>
        {created ? <CreatedPanel created={created} onClose={() => setCreated(null)} /> : null}

        <Card>
          <CardHeader title="Connect an environment" />
          <div className="grid gap-4 p-4 md:grid-cols-[1fr_1.3fr]">
            <div className="space-y-3">
              <Select value={platform} onChange={(e) => setPlatform(e.target.value as Platform)}>
                <option value="claude_code">Claude Code (hooks)</option>
                <option value="openai_agents">OpenAI Agents SDK</option>
                <option value="custom_sdk">Custom Python SDK</option>
              </Select>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. sam-laptop" />
              <Button variant="primary" onClick={create} disabled={busy}>
                {busy ? "Creating…" : "Create integration key"}
              </Button>
              {error ? <p className="text-xs text-[var(--failed)]">{error}</p> : null}
            </div>
            {info ? (
              <div className="space-y-2 text-xs">
                <div className="flex flex-wrap gap-1">
                  {info.capabilities.map((c) => (
                    <Badge key={c}>{c.replace(/_/g, " ")}</Badge>
                  ))}
                </div>
                <ul className="list-disc space-y-1 pl-4 text-muted">
                  {info.limitations.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </Card>

        <Card>
          <CardHeader title="Connected environments" />
          {integrations.error ? <ErrorState error={integrations.error} retry={integrations.refresh} /> : null}
          {integrations.loading && !integrations.data ? (
            <Loading />
          ) : !integrations.data?.length ? (
            <EmptyState title="Nothing connected yet" description="Create an integration key above to connect Claude Code or an Agents SDK app." />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {integrations.data.map((i) => {
                const c = CONNECTION[i.connection];
                return (
                  <li key={i.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <PlatformBadge platform={i.platform} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-ink">{i.name}</div>
                      <div className="text-[11px] text-faint">
                        {i.agents} agent{i.agents === 1 ? "" : "s"} · {i.live_sessions} live session
                        {i.live_sessions === 1 ? "" : "s"} · last seen {relativeTime(i.last_seen_at)}
                        {i.details?.host ? ` · ${String(i.details.host)}` : ""}
                      </div>
                    </div>
                    <span className="text-xs" style={{ color: c.color }}>
                      {c.label}
                    </span>
                    {!i.revoked_at ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          await api.revokeIntegration(i.id);
                          integrations.refresh();
                        }}
                      >
                        Revoke
                      </Button>
                    ) : null}
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
