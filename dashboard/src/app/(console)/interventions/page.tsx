"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { ActionBadge } from "@/components/platform";
import { Card, CardHeader, EmptyState, ErrorState, Loading, Mono } from "@/components/ui";

export default function InterventionsPage() {
  useTicker(5000);
  const decisions = useAsync(() => api.decisions({ limit: 200 }), [], { intervalMs: 10000 });
  const summary = useAsync(() => api.supervision(), [], { intervalMs: 10000 });
  useLiveMessages((m) => {
    if (m.type === "decision.created") decisions.refresh();
  });

  return (
    <>
      <PageHeader
        title="Interventions"
        description="Every time the supervisor changed — or wanted to change — what an agent did, with the rule, the reason, and what the platform could actually carry out."
      />
      <PageBody>
        {summary.data ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.data.decisions)
              .sort((a, b) => b[1] - a[1])
              .map(([action, count]) => (
                <Card key={action} className="flex items-center gap-2 px-3 py-2">
                  <ActionBadge action={action} />
                  <span className="font-mono text-sm tabular-nums text-ink">{count}</span>
                </Card>
              ))}
          </div>
        ) : null}
        <Card>
          <CardHeader
            title="Decision log"
            description="Allowed calls are recorded too, but only listed here when a rule wanted something stronger."
          />
          {decisions.error ? <ErrorState error={decisions.error} retry={decisions.refresh} /> : null}
          {decisions.loading && !decisions.data ? (
            <Loading />
          ) : !decisions.data?.length ? (
            <EmptyState title="No interventions yet" description="Nothing has needed more than an allow." />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {decisions.data.map((d) => (
                <li key={d.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <ActionBadge action={d.action} />
                    {d.intended_action ? (
                      <span className="text-faint">
                        wanted <ActionBadge action={d.intended_action} />
                      </span>
                    ) : null}
                    <Mono>{d.rule}</Mono>
                    {d.tool_name ? <span className="text-muted">on {d.tool_name}</span> : null}
                    <span className="ml-auto text-faint">
                      {d.mode} mode · {relativeTime(d.created_at)}
                      {d.latency_ms ? ` · ${Math.round(d.latency_ms)}ms` : ""}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-ink">{d.reason}</p>
                  {d.held_back_reason ? (
                    <p className="mt-0.5 text-[11px] text-[var(--warning)]">Held back: {d.held_back_reason}</p>
                  ) : null}
                  {d.feedback ? <p className="mt-0.5 text-[11px] text-muted">Told the agent: “{d.feedback}”</p> : null}
                  <Link href={`/executions/${d.execution_id}`} className="mt-1 inline-block text-[11px] text-faint hover:text-ink">
                    session →
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </PageBody>
    </>
  );
}
