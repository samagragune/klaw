"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { api, type TraceEvent } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLive, useLiveMessages } from "@/lib/live";
import { compactNumber, duration, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { EventTimeline } from "@/components/trace";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Loading,
  Mono,
  StatusBadge,
} from "@/components/ui";

/** Keep the feed bounded; an agent in a tight loop can emit thousands. */
const FEED_LIMIT = 300;

interface FeedEntry extends TraceEvent {
  executionId: string;
}

export default function MonitorPage() {
  useTicker(1000);
  const { status: liveStatus } = useLive();

  const active = useAsync(() => api.executions({ active: true, limit: 50 }), [], {
    intervalMs: 5000,
  });
  const agents = useAsync(() => api.agents(), [], { intervalMs: 30000 });
  const [feed, setFeed] = useState<FeedEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const agentName = useCallback(
    (agentId: string) => agents.data?.find((a) => a.id === agentId)?.name ?? agentId,
    [agents.data],
  );

  useLiveMessages((message) => {
    if (message.type === "execution.started") {
      active.refresh();
      return;
    }
    if (message.type !== "execution.updated") return;
    active.refresh();
    if (pausedRef.current) return;
    const executionId = String((message.execution as { id?: string }).id ?? "");
    const incoming = (message.events ?? []) as unknown as TraceEvent[];
    if (!incoming.length) return;
    setFeed((current) =>
      [...incoming.map((event) => ({ ...event, executionId })), ...current].slice(
        0,
        FEED_LIMIT,
      ),
    );
  });

  return (
    <>
      <PageHeader
        title="Live Monitor"
        description="Executions in flight, and the event stream as the backend broadcasts it."
        actions={
          <div className="flex items-center gap-2">
            <Badge>
              {liveStatus === "open" ? "socket connected" : `socket ${liveStatus}`}
            </Badge>
            <Button size="sm" onClick={() => setPaused(!paused)}>
              {paused ? "Resume feed" : "Pause feed"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setFeed([])}>
              Clear
            </Button>
          </div>
        }
      />
      <PageBody>
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
          <Card className="self-start">
            <CardHeader
              title="Active executions"
              description={`${active.data?.length ?? 0} running or paused`}
            />
            {active.loading && !active.data ? (
              <Loading />
            ) : !active.data?.length ? (
              <EmptyState
                title="Nothing running"
                description="Start one: python demo/run_demo.py long — then pause or cancel it from here."
              />
            ) : (
              <ul className="divide-y divide-[var(--border)]">
                {active.data.map((execution) => (
                  <li key={execution.id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link
                          href={`/executions/${execution.id}`}
                          className="block truncate text-sm font-medium text-ink hover:text-[var(--accent-soft)]"
                        >
                          {execution.task ?? "Untitled execution"}
                        </Link>
                        <div className="mt-0.5 truncate text-xs text-faint">
                          {agentName(execution.agent_id)} · started{" "}
                          {relativeTime(execution.started_at)}
                        </div>
                      </div>
                      <StatusBadge status={execution.status} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
                      <span>{duration(execution.started_at, null)} elapsed</span>
                      <span>{execution.tool_call_count} tool calls</span>
                      <span
                        className={
                          execution.error_count ? "text-[var(--failed)]" : undefined
                        }
                      >
                        {execution.error_count} errors
                      </span>
                      <span>{compactNumber(execution.token_usage)} tokens</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Event stream"
              description={
                paused
                  ? "Feed paused — new events are being skipped, not buffered."
                  : `${feed.length} events received in this session`
              }
            />
            {feed.length === 0 ? (
              <EmptyState
                title="Waiting for events"
                description="Events appear here the moment the backend broadcasts them. Nothing is simulated — an idle workspace shows an empty feed."
              />
            ) : (
              <div className="max-h-[640px] overflow-y-auto">
                <EventTimeline events={[...feed].reverse()} newestFirst />
              </div>
            )}
          </Card>
        </div>

        <p className="text-[11px] text-faint">
          <Mono>
            Streaming over a WebSocket bound to this workspace. Reconnects automatically
            if the backend restarts.
          </Mono>
        </p>
      </PageBody>
    </>
  );
}
