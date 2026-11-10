"use client";

import Link from "next/link";
import { useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { compactNumber, fullNumber, money, percent, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
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
    }, 1000);
  }, [overview, agents, executions]);

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
        description="Health of every agent connected to this workspace."
      />
      <PageBody>
        {overview.error ? (
          <Card>
            <ErrorState error={overview.error} retry={overview.refresh} />
          </Card>
        ) : null}

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Agents"
            value={metrics?.total_agents ?? "—"}
            hint={`${metrics?.running_agents ?? 0} active now`}
          />
          <Stat
            label="Running executions"
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
              description="Register one with the SDK, or run a bundled demo agent: python demo/run_demo.py healthy"
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
