"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TraceEvent } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import {
  compactNumber,
  duration,
  fullNumber,
  money,
  preciseTime,
  relativeTime,
} from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { EventTimeline } from "@/components/trace";
import { ExecutionControls, InterventionList } from "@/components/controls";
import { IncidentCard } from "@/components/incident";
import {
  Card,
  CardHeader,
  ErrorState,
  Loading,
  Mono,
  Stat,
  StatusBadge,
} from "@/components/ui";

export default function ExecutionPage() {
  const { id } = useParams<{ id: string }>();
  useTicker(2000);

  const execution = useAsync(() => api.execution(id), [id], { intervalMs: 10000 });
  const [liveEvents, setLiveEvents] = useState<TraceEvent[]>([]);
  const seenSeq = useRef<Set<number>>(new Set());
  const baselineSeq = useRef<number | null>(null);

  // Reset the live buffer when the underlying execution is re-fetched, so an
  // event is never shown twice from two sources.
  useEffect(() => {
    if (!execution.data) return;
    const fetched = execution.data.events.map((event) => event.seq);
    seenSeq.current = new Set(fetched);
    if (baselineSeq.current === null) {
      baselineSeq.current = fetched.length ? Math.max(...fetched) : -1;
    }
    setLiveEvents((current) =>
      current.filter((event) => !seenSeq.current.has(event.seq)),
    );
  }, [execution.data]);

  useLiveMessages((message) => {
    if (message.type !== "execution.updated") return;
    if ((message.execution as { id?: string }).id !== id) return;
    const incoming = (message.events ?? []) as unknown as TraceEvent[];
    setLiveEvents((current) => {
      const merged = [...current];
      for (const event of incoming) {
        if (seenSeq.current.has(event.seq)) continue;
        seenSeq.current.add(event.seq);
        merged.push(event);
      }
      return merged.sort((a, b) => a.seq - b.seq);
    });
    // Status, counters and incidents are server-computed; pull them fresh.
    execution.refresh();
  });

  const refresh = useCallback(() => execution.refresh(), [execution]);

  if (execution.loading && !execution.data) {
    return (
      <>
        <PageHeader title="Execution" />
        <Loading />
      </>
    );
  }
  if (execution.error || !execution.data) {
    return (
      <>
        <PageHeader title="Execution" />
        <PageBody>
          <Card>
            <ErrorState
              error={execution.error ?? "Execution not found"}
              retry={execution.refresh}
            />
          </Card>
        </PageBody>
      </>
    );
  }

  const data = execution.data;
  const events = [...data.events, ...liveEvents].sort((a, b) => a.seq - b.seq);
  const live = data.status === "running" || data.status === "paused";

  return (
    <>
      <PageHeader
        breadcrumb={
          <Link href={`/agents/${data.agent_id}`} className="hover:text-ink">
            {data.agent_name}
          </Link>
        }
        title={data.task ?? "Untitled execution"}
        description={<Mono>{data.id}</Mono>}
        actions={
          <div className="flex items-center gap-3">
            <StatusBadge status={data.status} />
            <ExecutionControls execution={data} onChange={refresh} />
          </div>
        }
      />
      <PageBody>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat
            label="Duration"
            value={duration(data.started_at, data.completed_at)}
            hint={
              live
                ? `runtime last heard from ${relativeTime(
                    data.last_heartbeat_at ?? data.last_event_at ?? data.started_at,
                  )}`
                : relativeTime(data.completed_at)
            }
          />
          <Stat label="Tool calls" value={data.tool_call_count} />
          <Stat
            label="Errors"
            value={data.error_count}
            tone={data.error_count ? "danger" : "default"}
          />
          <Stat
            label="Tokens"
            value={compactNumber(data.token_usage)}
            hint={`${fullNumber(data.prompt_tokens)} in / ${fullNumber(data.completion_tokens)} out`}
          />
          <Stat
            label="Estimated cost"
            value={money(data.estimated_cost)}
            hint={data.cost_complete ? undefined : "partial — unpriced model used"}
          />
        </div>

        {data.error ? (
          <Card className="border-[#4a2023] bg-[#1a1012] px-4 py-3">
            <div className="text-[11px] uppercase tracking-wide text-[var(--failed)]">
              {data.status === "cancelled" ? "Cancellation reason" : "Failure"}
            </div>
            <p className="mt-1 font-mono text-xs text-[#fca5a5]">{data.error}</p>
          </Card>
        ) : null}

        {data.output_summary ? (
          <Card className="px-4 py-3">
            <div className="text-[11px] uppercase tracking-wide text-faint">Output</div>
            <p className="mt-1 text-sm text-ink">{data.output_summary}</p>
          </Card>
        ) : null}

        {data.incidents.length ? (
          <div className="space-y-3">
            {data.incidents.map((incident) => (
              <IncidentCard
                key={incident.id}
                incident={{
                  ...incident,
                  agent_name: data.agent_name,
                  execution_status: data.status,
                  execution_task: data.task,
                }}
                onChange={refresh}
                showLinks={false}
                defaultExpanded
              />
            ))}
          </div>
        ) : null}

        <Card>
          <CardHeader
            title="Intervention history"
            description="A command is only reported as applied once the runtime confirms it."
          />
          <InterventionList interventions={data.interventions} />
        </Card>

        <Card>
          <CardHeader
            title="Execution trace"
            description={`${events.length} events${live ? " · streaming live" : ""}`}
            action={
              events.length ? (
                <Mono>last {preciseTime(events[events.length - 1].timestamp)}</Mono>
              ) : null
            }
          />
          <div className="max-h-[560px] overflow-y-auto">
            <EventTimeline
              events={events}
              highlightAfterSeq={baselineSeq.current ?? undefined}
            />
          </div>
        </Card>
      </PageBody>
    </>
  );
}
