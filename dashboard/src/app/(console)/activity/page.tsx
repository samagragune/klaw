"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { api, type ActivityEvent } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { PlatformBadge } from "@/components/platform";
import { Card, EmptyState, ErrorState, Loading, Select } from "@/components/ui";

const FAMILIES: Record<string, string[]> = {
  all: [],
  tools: ["TOOL_CALL_STARTED", "TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED", "TOOL_CALL_BLOCKED"],
  supervisor: ["SUPERVISOR_DECISION", "APPROVAL_REQUESTED", "APPROVAL_RESOLVED", "AGENT_STOPPED", "SESSION_PAUSED", "SESSION_RESUMED"],
  tasks: ["PROMPT_SUBMITTED", "TASK_CREATED", "TASK_COMPLETED", "SESSION_STARTED", "SESSION_ENDED"],
  agents: ["AGENT_HANDOFF", "SUBAGENT_STARTED", "SUBAGENT_COMPLETED", "AGENT_ERROR"],
};

function describe(e: ActivityEvent): string {
  const p = e.payload ?? {};
  const s = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  const input = (p.tool_input ?? {}) as Record<string, unknown>;
  switch (e.event_type) {
    case "TOOL_CALL_STARTED":
      return `${s(p.tool_name)} ${s(input.command ?? input.file_path ?? input.path ?? "").slice(0, 90)}`;
    case "TOOL_CALL_FAILED":
      return `${s(p.tool_name)} failed: ${s(p.error).slice(0, 90)}`;
    case "TOOL_CALL_BLOCKED":
      return `${s(p.tool_name)} not run — ${s(p.reason).slice(0, 90)}`;
    case "SUPERVISOR_DECISION":
      return `${s(p.action)} · ${s(p.rule)}`;
    case "PROMPT_SUBMITTED":
      return s(p.prompt).slice(0, 120);
    case "TASK_COMPLETED":
      return s(p.output).slice(0, 120);
    case "AGENT_HANDOFF":
      return `${s(p.from_agent)} → ${s(p.to_agent)}`;
    case "MODEL_CALL":
      return `${s(p.model)} · ${s(p.prompt_tokens)}/${s(p.completion_tokens)} tok`;
    default:
      return s(p.message ?? p.reason ?? p.tool_name ?? p.status ?? "");
  }
}

export default function ActivityPage() {
  useTicker(5000);
  const [family, setFamily] = useState("all");
  const [platform, setPlatform] = useState("all");
  const feed = useAsync(() => api.activity(200), [], { intervalMs: 15000 });
  const [live, setLive] = useState<ActivityEvent[]>([]);

  useLiveMessages((m) => {
    if (m.type !== "execution.updated") return;
    const ex = m.execution as { id: string; agent_id: string; platform: string };
    const incoming = ((m.events ?? []) as unknown as ActivityEvent[]).map((e) => ({
      ...e,
      execution_id: ex.id,
      agent_id: ex.agent_id,
      platform: ex.platform as ActivityEvent["platform"],
      agent_name: "",
    }));
    setLive((current) => [...incoming.reverse(), ...current].slice(0, 200));
  });

  const rows = useMemo(() => {
    const seen = new Set<string>();
    const names = new Map((feed.data ?? []).map((e) => [e.agent_id, e.agent_name]));
    return [...live, ...(feed.data ?? [])]
      .filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)))
      .map((e) => ({ ...e, agent_name: e.agent_name || names.get(e.agent_id) || "" }))
      .filter((e) => family === "all" || FAMILIES[family].includes(e.event_type))
      .filter((e) => platform === "all" || e.platform === platform)
      .slice(0, 200);
  }, [live, feed.data, family, platform]);

  return (
    <>
      <PageHeader
        title="Live activity"
        description="Everything every connected agent is doing, as it happens."
        actions={
          <>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="all">All platforms</option>
              <option value="claude_code">Claude Code</option>
              <option value="openai_agents">OpenAI Agents</option>
              <option value="custom_sdk">Custom SDK</option>
            </Select>
            <Select value={family} onChange={(e) => setFamily(e.target.value)}>
              {Object.keys(FAMILIES).map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
          </>
        }
      />
      <PageBody>
        {feed.error ? <ErrorState error={feed.error} retry={feed.refresh} /> : null}
        <Card>
          {feed.loading && !feed.data ? (
            <Loading />
          ) : !rows.length ? (
            <EmptyState title="No activity yet" description="Connect an integration and run an agent." />
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {rows.map((e) => (
                <li key={e.id} className="row-in flex items-center gap-3 px-4 py-2 text-xs">
                  <span className="w-16 shrink-0 text-faint">{relativeTime(e.timestamp)}</span>
                  <span className="hidden w-36 shrink-0 sm:block">
                    <PlatformBadge platform={e.platform} />
                  </span>
                  <span className="w-44 shrink-0 truncate font-mono text-[11px] text-muted">{e.event_type}</span>
                  <Link
                    href={`/executions/${e.execution_id}`}
                    className="min-w-0 flex-1 truncate text-ink hover:underline"
                    title={describe(e)}
                  >
                    {describe(e) || "—"}
                  </Link>
                  <span className="hidden max-w-48 shrink-0 truncate text-faint md:block">{e.agent_name}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </PageBody>
    </>
  );
}
