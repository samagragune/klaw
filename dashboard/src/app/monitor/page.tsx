"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { api, PLATFORM_LABELS } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { compactNumber, duration, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { PlatformBadge } from "@/components/platform";
import { Card, EmptyState, ErrorState, Loading, Select, StatusBadge, Table, Td, Th } from "@/components/ui";

const STATUSES = ["running", "paused", "idle", "inactive", "completed", "failed", "cancelled"];

/**
 * Every session from every connected platform. A session is one Claude Code
 * session (many prompts), one Agents SDK run, or one SDK monitored call.
 */
export default function SessionsPage() {
  useTicker(5000);
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const sessions = useAsync(
    () => api.executions({ platform: platform || undefined, status: status || undefined, limit: 200 }),
    [platform, status],
    { intervalMs: 15000 },
  );
  const agents = useAsync(() => api.agents(), [], { intervalMs: 60000 });
  const names = new Map((agents.data ?? []).map((a) => [a.id, a.name]));

  // Coalesce live updates into one refresh per second.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshSoon = useCallback(() => {
    if (pending.current) return;
    pending.current = setTimeout(() => {
      pending.current = null;
      sessions.refresh();
    }, 1000);
  }, [sessions]);
  useLiveMessages((m) => {
    if (m.type === "execution.updated" || m.type === "execution.started") refreshSoon();
  });

  const rows = sessions.data ?? [];
  const live = rows.filter((s) => ["running", "paused"].includes(s.status)).length;

  return (
    <>
      <PageHeader
        title="Sessions"
        description={`Every session from every connected agent, newest first. ${live ? `${live} running now.` : ""}`}
        actions={
          <>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="">All platforms</option>
              {Object.entries(PLATFORM_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </>
        }
      />
      <PageBody>
        {sessions.error ? <ErrorState error={sessions.error} retry={sessions.refresh} /> : null}
        <Card>
          {sessions.loading && !sessions.data ? (
            <Loading />
          ) : !rows.length ? (
            <EmptyState
              title="No sessions match"
              description="Sessions appear as soon as a connected agent sends its first event."
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Session</Th>
                  <Th>Platform</Th>
                  <Th>Status</Th>
                  <Th>Tools</Th>
                  <Th>Errors</Th>
                  <Th>Tokens</Th>
                  <Th>Duration</Th>
                  <Th>Started</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className="border-b border-line last:border-0 hover:bg-panel-raised">
                    <Td>
                      <Link href={`/executions/${s.id}`} className="block max-w-md">
                        <span className="line-clamp-1 text-ink hover:underline">
                          {s.task ?? <span className="text-faint">(no objective yet)</span>}
                        </span>
                        <span className="text-[11px] text-faint">{names.get(s.agent_id) ?? "—"}</span>
                      </Link>
                    </Td>
                    <Td>
                      <PlatformBadge platform={s.platform} />
                    </Td>
                    <Td>
                      <StatusBadge status={s.status} />
                    </Td>
                    <Td className="tabular-nums">{s.tool_call_count}</Td>
                    <Td className="tabular-nums">
                      <span style={{ color: s.error_count ? "var(--failed)" : undefined }}>{s.error_count}</span>
                    </Td>
                    <Td className="tabular-nums">{s.token_usage ? compactNumber(s.token_usage) : "—"}</Td>
                    <Td className="text-xs text-muted">{duration(s.started_at, s.completed_at)}</Td>
                    <Td className="text-xs text-faint">{relativeTime(s.started_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <p className="text-[11px] text-faint">
          Claude Code sessions stay <em>idle</em> between prompts and become <em>inactive</em> after a
          long silence — never failed, because hooks only fire on activity.{" "}
          {PLATFORM_LABELS.claude_code}: sessions end when Claude Code exits.
        </p>
      </PageBody>
    </>
  );
}
