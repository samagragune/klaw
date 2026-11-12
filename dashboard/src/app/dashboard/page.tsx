"use client";

import Link from "next/link";
import { useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { compactNumber, fullNumber, money, percent, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { ActionBadge, PlatformBadge } from "@/components/platform";
import { ExecutionVolumeChart } from "@/components/charts";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Loading,
  Stat,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";

export default function OverviewPage() {
  useTicker(10000);

  const overview = useAsync(() => api.overview(), [], { intervalMs: 15000 });
  const agents = useAsync(() => api.agents(), [], { intervalMs: 20000 });
  const executions = useAsync(() => api.executions({ limit: 200 }), [], {
    intervalMs: 30000,
  });
  const platforms = useAsync(() => api.platformOverview(), [], { intervalMs: 15000 });
  const supervision = useAsync(() => api.supervision(), [], { intervalMs: 10000 });

  // Live events arrive far faster than it is worth re-querying rollups, so
  // coalesce them into at most one refresh per second.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshSoon = useCallback(() => {
    if (pending.current) return;
    pending.current = setTimeout(() => {
      pending.current = null;
      overview.refresh();
      agents.refresh();
      executions.refresh();
      platforms.refresh();
      supervision.refresh();
    }, 1000);
  }, [overview, agents, executions, platforms, supervision]);

  useLiveMessages((message) => {
    if (message.type !== "heartbeat") refreshSoon();
  });

  const metrics = overview.data;
  const finished =
    (metrics?.executions_succeeded ?? 0) +
    (metrics?.executions_failed ?? 0) +
    (metrics?.executions_cancelled ?? 0);
  const successRate = finished ? (metrics?.executions_succeeded ?? 0) / finished : null;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Every connected agent, across every platform, in one place."
      />
      <PageBody>
        {overview.error ? (
          <Card>
            <ErrorState error={overview.error} retry={overview.refresh} />
          </Card>
        ) : null}

        {platforms.data && platforms.data.length ? (
          <div className="grid gap-3 md:grid-cols-3">
            {platforms.data.map((p) => (
              <Card key={p.platform} className="p-4">
                <div className="flex items-center justify-between">
                  <PlatformBadge platform={p.platform} />
                  <span className="text-[11px] text-faint">
                    {p.integrations} integration{p.integrations === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  <div>
                    <div className="text-[11px] text-faint">Agents</div>
                    <div className="text-lg tabular-nums text-ink">{p.agents}</div>
                  </div>
                  <div>
                    <div className="text-[11px] text-faint">Live sessions</div>
                    <div className="text-lg tabular-nums" style={{ color: p.live_sessions ? "var(--success)" : undefined }}>
                      {p.live_sessions}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-faint">Open issues</div>
                    <div className="text-lg tabular-nums" style={{ color: p.open_incidents ? "var(--failed)" : undefined }}>
                      {p.open_incidents}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <Card>
            <EmptyState
              title="No agent environments connected"
              description="Connect Claude Code or an OpenAI Agents SDK app under Integrations to start supervising."
            />
          </Card>
        )}

        {supervision.data ? (
          <div className="flex flex-wrap items-center gap-2">
            <Link href="/approvals">
              <Card className="flex items-center gap-2 px-3 py-2 hover:border-line-strong">
                <span className="text-xs text-muted">Waiting for approval</span>
                <span
                  className="font-mono text-sm tabular-nums"
                  style={{ color: supervision.data.pending_approvals ? "var(--warning)" : undefined }}
                >
                  {supervision.data.pending_approvals}
                </span>
              </Card>
            </Link>
            {Object.entries(supervision.data.decisions)
              .filter(([a]) => a !== "ALLOW")
              .map(([action, count]) => (
                <Link key={action} href="/interventions">
                  <Card className="flex items-center gap-2 px-3 py-2 hover:border-line-strong">
                    <ActionBadge action={action} />
                    <span className="font-mono text-sm tabular-nums text-ink">{count}</span>
                  </Card>
                </Link>
              ))}
            <Card className="flex items-center gap-2 px-3 py-2">
              <span className="text-xs text-muted">Tool calls checked</span>
              <span className="font-mono text-sm tabular-nums text-ink">
                {Object.values(supervision.data.decisions).reduce((a, b) => a + b, 0)}
              </span>
            </Card>
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Agents"
            value={metrics?.total_agents ?? "—"}
            hint={`${metrics?.running_agents ?? 0} active now`}
          />
          <Stat
            label="Live sessions"
            value={metrics?.running_executions ?? "—"}
            hint={`${metrics?.executions_total ?? 0} total`}
            tone={metrics?.running_executions ? "success" : "default"}
          />
          <Stat
            label="Open incidents"
            value={metrics?.open_incidents ?? "—"}
            tone={metrics?.open_incidents ? "danger" : "default"}
            hint={metrics?.open_incidents ? "needs review" : "none outstanding"}
          />
          <Stat
            label="Success rate"
            value={percent(successRate)}
            hint={
              finished
                ? `${metrics?.executions_succeeded} of ${finished} finished`
                : "nothing finished yet"
            }
          />
          <Stat
            label="Succeeded"
            value={metrics?.executions_succeeded ?? "—"}
            tone="success"
          />
          <Stat label="Failed" value={metrics?.executions_failed ?? "—"} tone="danger" />
          <Stat
            label="Tokens"
            value={compactNumber(metrics?.total_tokens ?? 0)}
            hint={fullNumber(metrics?.total_tokens ?? 0)}
          />
          <Stat
            label="Estimated cost"
            value={money(metrics?.estimated_cost)}
            hint={
              metrics && !metrics.cost_complete
                ? "partial — some models have no known price"
                : "from reported token usage"
            }
          />
        </div>

        <ExecutionVolumeChart executions={executions.data ?? []} />

        <Card>
          <CardHeader
            title="Agents"
            description="Every agent that has registered with this workspace."
          />
          {agents.loading && !agents.data ? (
            <Loading />
          ) : agents.error ? (
            <ErrorState error={agents.error} retry={agents.refresh} />
          ) : !agents.data?.length ? (
            <EmptyState
              title="No agents connected"
              description="Connect an integration to see agents here."
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Agent</Th>
                  <Th>Framework</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Executions</Th>
                  <Th className="text-right">Success rate</Th>
                  <Th className="text-right">Tokens</Th>
                  <Th className="text-right">Incidents</Th>
                  <Th className="text-right">Last activity</Th>
                </tr>
              </thead>
              <tbody>
                {agents.data.map((agent) => (
                  <tr key={agent.id} className="hover:bg-panel-raised">
                    <Td>
                      <Link
                        href={`/agents/${agent.id}`}
                        className="font-medium text-ink hover:text-[var(--accent-soft)]"
                      >
                        {agent.name}
                      </Link>
                      {agent.description ? (
                        <div className="truncate text-xs text-faint">
                          {agent.description}
                        </div>
                      ) : null}
                    </Td>
                    <Td className="text-muted">{agent.framework}</Td>
                    <Td>
                      <StatusBadge status={agent.status} kind="agent" />
                    </Td>
                    <Td className="text-right tabular-nums text-muted">
                      {agent.execution_count}
                    </Td>
                    <Td className="text-right tabular-nums text-muted">
                      {percent(agent.success_rate)}
                    </Td>
                    <Td className="text-right tabular-nums text-muted">
                      {compactNumber(agent.total_tokens)}
                    </Td>
                    <Td className="text-right tabular-nums">
                      {agent.open_incident_count > 0 ? (
                        <span className="text-[var(--failed)]">
                          {agent.open_incident_count}
                        </span>
                      ) : (
                        <span className="text-faint">0</span>
                      )}
                    </Td>
                    <Td className="text-right text-muted">
                      {relativeTime(agent.last_activity_at)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </PageBody>
    </>
  );
}
