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
import { ActionBadge, PlatformBadge } from "@/components/platform";
import {
  Button,
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
  const [evaluating, setEvaluating] = useState(false);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);
  const evaluateOutput = useCallback(async () => {
    setEvaluating(true);
    setEvaluationError(null);
    try {
      await api.evaluateOutput(id);
      refresh();
    } catch (err) {
      setEvaluationError(err instanceof Error ? err.message : "Evaluation failed");
    } finally {
      setEvaluating(false);
    }
  }, [id, refresh]);

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
  const live = data.status === "running" || data.status === "paused" || data.status === "idle";

  return (
    <>
      <PageHeader
        breadcrumb={
          <Link href={`/agents/${data.agent_id}`} className="hover:text-ink">
            {data.agent_name}
          </Link>
        }
        title={data.task ?? "Untitled session"}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <PlatformBadge platform={data.platform} />
            <Mono>{data.external_session_id ?? data.id}</Mono>
            {data.context?.cwd ? <Mono>{String(data.context.cwd)}</Mono> : null}
          </span>
        }
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
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] uppercase tracking-wide text-faint">Output</div>
              {data.status === "completed" ? (
                <Button size="sm" variant="ghost" disabled={evaluating} onClick={evaluateOutput}>
                  {evaluating
                    ? "Evaluating…"
                    : data.output_evaluation
                      ? "Re-evaluate output"
                      : "Evaluate output"}
                </Button>
              ) : null}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-sm text-ink">{data.output_summary}</p>
            {data.output_evaluation ? (
              <div className="mt-3 border-t border-[var(--border)] pt-2">
                <div
                  className={
                    data.output_evaluation.satisfies_objective === false
                      ? "text-xs text-[var(--warning)]"
                      : data.output_evaluation.satisfies_objective
                        ? "text-xs text-[var(--success)]"
                        : "text-xs text-muted"
                  }
                >
                  {data.output_evaluation.satisfies_objective === false
                    ? "Does not satisfy the task"
                    : data.output_evaluation.satisfies_objective
                      ? "Satisfies the task"
                      : "Evaluator could not decide"}
                  {data.output_evaluation.confidence
                    ? ` · confidence ${data.output_evaluation.confidence}`
                    : ""}
                </div>
                {data.output_evaluation.reasoning ? (
                  <p className="mt-1 text-xs leading-relaxed text-muted">
                    {data.output_evaluation.reasoning}
                  </p>
                ) : null}
                {data.output_evaluation.gaps?.length ? (
                  <ul className="mt-1 list-disc pl-4 text-xs text-muted">
                    {data.output_evaluation.gaps.map((gap) => (
                      <li key={gap}>{gap}</li>
                    ))}
                  </ul>
                ) : null}
                {data.output_evaluation.evaluator ? (
                  <p className="mt-1 font-mono text-[10px] text-faint">
                    evaluated by {data.output_evaluation.evaluator}
                  </p>
                ) : null}
              </div>
            ) : null}
            {evaluationError ? (
              <p className="mt-2 text-[11px] text-[var(--failed)]">{evaluationError}</p>
            ) : null}
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

        {data.tasks.length ? (
          <Card>
            <CardHeader
              title={`Tasks (${data.tasks.length})`}
              description="Each objective this session worked on, in order."
            />
            <ul className="divide-y divide-[var(--border)]">
              {data.tasks.map((task) => (
                <li key={task.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-medium capitalize text-ink">{task.status}</span>
                    {task.redirects ? <span className="text-[var(--running)]">sent back ×{task.redirects}</span> : null}
                    <span className="text-faint">
                      {task.tool_call_count} tools · {task.model_calls} model calls
                      {task.error_count ? ` · ${task.error_count} errors` : ""} · {duration(task.started_at, task.completed_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-ink">{task.objective ?? <span className="text-faint">(no objective reported)</span>}</p>
                  {task.output_summary ? (
                    <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs text-muted">{task.output_summary}</p>
                  ) : null}
                  {task.evaluation?.satisfies_objective === false ? (
                    <p className="mt-1 text-[11px] text-[var(--warning)]">
                      Output review: misses the task — {(task.evaluation.gaps ?? []).join("; ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {data.decisions.some((d) => d.action !== "ALLOW" || d.intended_action) ? (
          <Card>
            <CardHeader
              title="Supervisor decisions"
              description={`${data.decisions.length} decision points checked; the ones that did more than allow are listed.`}
            />
            <ul className="divide-y divide-[var(--border)]">
              {data.decisions
                .filter((d) => d.action !== "ALLOW" || d.intended_action)
                .map((d) => (
                  <li key={d.id} className="px-4 py-2.5 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <ActionBadge action={d.action} />
                      {d.intended_action ? <span className="text-faint">wanted {d.intended_action}</span> : null}
                      <Mono>{d.rule}</Mono>
                      {d.tool_name ? <span className="text-muted">{d.tool_name}</span> : null}
                      <span className="ml-auto text-faint">{relativeTime(d.created_at)}</span>
                    </div>
                    <p className="mt-1 text-ink">{d.reason}</p>
                    {d.held_back_reason ? <p className="text-[11px] text-[var(--warning)]">{d.held_back_reason}</p> : null}
                  </li>
                ))}
            </ul>
          </Card>
        ) : null}

        {data.approvals.length ? (
          <Card>
            <CardHeader title="Approval requests" />
            <ul className="divide-y divide-[var(--border)]">
              {data.approvals.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-xs">
                  <span className="w-16 font-medium capitalize text-ink">{a.status}</span>
                  <Mono>{a.tool_name}</Mono>
                  <span className="min-w-0 flex-1 truncate text-muted">{a.reason}</span>
                  <span className="text-faint">{a.decided_by ?? (a.status === "pending" ? "waiting…" : "")}</span>
                  {a.status === "pending" ? (
                    <Link href="/approvals" className="text-[var(--warning)] hover:underline">
                      answer →
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </Card>
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
