"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { compactNumber, money, percent } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { PlatformBadge, ScoreBadge } from "@/components/platform";
import { Card, CardHeader, EmptyState, ErrorState, Loading, Mono, Stat, Table, Td, Th } from "@/components/ui";

export default function ReliabilityPage() {
  const report = useAsync(() => api.reliability(), [], { intervalMs: 30000 });
  const costs = useAsync(() => api.evaluationCosts(30), [], { intervalMs: 30000 });

  return (
    <>
      <PageHeader
        title="Reliability"
        description="Scores are a fixed formula over stored counts, so a change can always be explained."
      />
      <PageBody>
        <Card>
          <CardHeader
            title="Agent reliability"
            description={report.data ? <Mono>{report.data.formula}</Mono> : undefined}
          />
          {report.error ? <ErrorState error={report.error} retry={report.refresh} /> : null}
          {report.loading && !report.data ? (
            <Loading />
          ) : !report.data?.agents.length ? (
            <EmptyState title="No tasks yet" description="Scores appear once agents finish tasks." />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Agent</Th>
                  <Th>Score</Th>
                  <Th>Task success</Th>
                  <Th>Incident-free</Th>
                  <Th>Tool success</Th>
                  <Th>Finished tasks</Th>
                </tr>
              </thead>
              <tbody>
                {report.data.agents.map((a) => (
                  <tr key={a.agent_id} className="border-b border-line last:border-0">
                    <Td>
                      <div className="flex items-center gap-2">
                        <PlatformBadge platform={a.platform} />
                        <Link href={`/agents/${a.agent_id}`} className="text-ink hover:underline">
                          {a.name}
                        </Link>
                      </div>
                    </Td>
                    <Td>
                      <ScoreBadge score={a.score} />
                    </Td>
                    <Td className="tabular-nums">{percent(a.components.task_success)}</Td>
                    <Td className="tabular-nums">{percent(a.components.clean_rate)}</Td>
                    <Td className="tabular-nums">{percent(a.components.tool_success)}</Td>
                    <Td className="tabular-nums">
                      {a.finished_tasks}
                      {a.running_tasks ? <span className="text-faint"> (+{a.running_tasks} running)</span> : null}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader
            title="What supervision costs"
            description="Every supervisor-LLM call over the last 30 days. Repeated identical questions are answered from cache at no cost."
          />
          {costs.data ? (
            <>
              <div className="grid grid-cols-2 gap-px bg-[var(--border)] sm:grid-cols-4">
                <div className="bg-panel p-4">
                  <Stat label="Evaluator calls" value={compactNumber(costs.data.calls)} />
                </div>
                <div className="bg-panel p-4">
                  <Stat label="Answered from cache" value={compactNumber(costs.data.cached)} />
                </div>
                <div className="bg-panel p-4">
                  <Stat label="Tokens" value={compactNumber(costs.data.tokens)} />
                </div>
                <div className="bg-panel p-4">
                  <Stat
                    label="Estimated cost"
                    value={money(costs.data.estimated_cost)}
                    hint={costs.data.cost_complete ? undefined : "partial — some models unpriced"}
                  />
                </div>
              </div>
              {costs.data.breakdown.length ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Purpose</Th>
                      <Th>Model</Th>
                      <Th>Calls</Th>
                      <Th>Cached</Th>
                      <Th>Failed</Th>
                      <Th>Tokens</Th>
                      <Th>Avg latency</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {costs.data.breakdown.map((b) => (
                      <tr key={`${b.purpose}-${b.model}`} className="border-b border-line last:border-0">
                        <Td>{b.purpose.replace(/_/g, " ")}</Td>
                        <Td>
                          <Mono>{b.model}</Mono>
                        </Td>
                        <Td className="tabular-nums">{b.calls}</Td>
                        <Td className="tabular-nums">{b.cached}</Td>
                        <Td className="tabular-nums">{b.failed}</Td>
                        <Td className="tabular-nums">{compactNumber(b.prompt_tokens + b.completion_tokens)}</Td>
                        <Td className="tabular-nums">{b.avg_latency_ms ? `${(b.avg_latency_ms / 1000).toFixed(1)}s` : "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : null}
            </>
          ) : costs.loading ? (
            <Loading />
          ) : null}
        </Card>
      </PageBody>
    </>
  );
}
