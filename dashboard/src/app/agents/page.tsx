"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { compactNumber, money, percent, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  Loading,
  Mono,
  StatusBadge,
} from "@/components/ui";

export default function AgentsPage() {
  useTicker(10000);
  const agents = useAsync(() => api.agents(), [], { intervalMs: 10000 });

  return (
    <>
      <PageHeader
        title="Agents"
        description="Every agent that has registered with this workspace through the SDK."
      />
      <PageBody>
        {agents.loading && !agents.data ? (
          <Loading />
        ) : agents.error ? (
          <Card>
            <ErrorState error={agents.error} retry={agents.refresh} />
          </Card>
        ) : !agents.data?.length ? (
          <Card>
            <EmptyState
              title="No agents yet"
              description="An agent registers the first time it runs under @supervisor.monitor. Try: python demo/run_demo.py healthy"
            />
          </Card>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {agents.data.map((agent) => (
              <Link key={agent.id} href={`/agents/${agent.id}`}>
                <Card className="h-full p-4 transition-colors hover:border-line-strong hover:bg-panel-raised">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-medium text-ink">
                        {agent.name}
                      </h2>
                      <Mono className="mt-0.5 block truncate">{agent.framework}</Mono>
                    </div>
                    <StatusBadge status={agent.status} kind="agent" />
                  </div>

                  {agent.description ? (
                    <p className="mt-2 line-clamp-2 text-xs text-muted">
                      {agent.description}
                    </p>
                  ) : null}

                  <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
                    <div>
                      <dt className="text-[11px] text-faint">Executions</dt>
                      <dd className="text-sm tabular-nums text-ink">
                        {agent.execution_count}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-faint">Success rate</dt>
                      <dd className="text-sm tabular-nums text-ink">
                        {percent(agent.success_rate)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-faint">Tokens</dt>
                      <dd className="text-sm tabular-nums text-ink">
                        {compactNumber(agent.total_tokens)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-faint">Est. cost</dt>
                      <dd className="text-sm tabular-nums text-ink">
                        {money(agent.estimated_cost)}
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
                    <span className="text-[11px] text-faint">
                      {relativeTime(agent.last_activity_at)}
                    </span>
                    {agent.open_incident_count > 0 ? (
                      <Badge className="border-[#4a2023] text-[var(--failed)]">
                        {agent.open_incident_count} open incident
                        {agent.open_incident_count === 1 ? "" : "s"}
                      </Badge>
                    ) : (
                      <span className="text-[11px] text-faint">no open incidents</span>
                    )}
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}
