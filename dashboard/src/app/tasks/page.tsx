"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { duration, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { Card, EmptyState, ErrorState, Loading, Select, Table, Td, Th } from "@/components/ui";

const TASK_TONES: Record<string, string> = {
  running: "var(--running)",
  completed: "var(--success)",
  failed: "var(--failed)",
  cancelled: "var(--cancelled)",
  abandoned: "var(--idle)",
};

export default function TasksPage() {
  useTicker(5000);
  const [status, setStatus] = useState("");
  const tasks = useAsync(() => api.tasks({ status: status || undefined, limit: 200 }), [status], {
    intervalMs: 8000,
  });
  const agents = useAsync(() => api.agents(), []);
  const names = new Map((agents.data ?? []).map((a) => [a.id, a.name]));

  return (
    <>
      <PageHeader
        title="Tasks"
        description="One task is one objective: a Claude Code prompt through to its stop, an Agents SDK run, an SDK monitored call. Abandoned means a new objective arrived before this one reported finishing."
        actions={
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {Object.keys(TASK_TONES).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        }
      />
      <PageBody>
        {tasks.error ? <ErrorState error={tasks.error} retry={tasks.refresh} /> : null}
        <Card>
          {tasks.loading && !tasks.data ? (
            <Loading />
          ) : !tasks.data?.length ? (
            <EmptyState title="No tasks" description="Tasks appear when an agent receives an objective." />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Objective</Th>
                  <Th>Agent</Th>
                  <Th>Status</Th>
                  <Th>Tools</Th>
                  <Th>Model calls</Th>
                  <Th>Review</Th>
                  <Th>Duration</Th>
                  <Th>Started</Th>
                </tr>
              </thead>
              <tbody>
                {tasks.data.map((t) => (
                  <tr key={t.id} className="border-b border-line last:border-0">
                    <Td>
                      <Link href={`/executions/${t.execution_id}`} className="line-clamp-2 max-w-md text-ink hover:underline">
                        {t.objective ?? <span className="text-faint">(no objective reported)</span>}
                      </Link>
                    </Td>
                    <Td className="text-xs text-muted">{names.get(t.agent_id) ?? "—"}</Td>
                    <Td>
                      <span className="text-xs font-medium capitalize" style={{ color: TASK_TONES[t.status] }}>
                        {t.status}
                        {t.redirects ? ` · sent back ×${t.redirects}` : ""}
                      </span>
                    </Td>
                    <Td className="tabular-nums">
                      {t.tool_call_count}
                      {t.error_count ? <span className="text-[var(--failed)]"> · {t.error_count} err</span> : null}
                    </Td>
                    <Td className="tabular-nums">{t.model_calls}</Td>
                    <Td className="text-xs">
                      {t.evaluation ? (
                        <span
                          style={{
                            color:
                              t.evaluation.satisfies_objective === false
                                ? "var(--warning)"
                                : t.evaluation.satisfies_objective
                                  ? "var(--success)"
                                  : "var(--idle)",
                          }}
                        >
                          {t.evaluation.satisfies_objective === false
                            ? "misses task"
                            : t.evaluation.satisfies_objective
                              ? "meets task"
                              : "undecided"}
                        </span>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </Td>
                    <Td className="text-xs text-muted">{duration(t.started_at, t.completed_at)}</Td>
                    <Td className="text-xs text-faint">{relativeTime(t.started_at)}</Td>
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
