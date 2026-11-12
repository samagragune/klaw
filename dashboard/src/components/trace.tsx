"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { TraceEvent } from "@/lib/api";
import { preciseTime } from "@/lib/format";
import { cn, Mono } from "./ui";

/** Colour by event family, so a long trace is scannable at a glance. */
function toneFor(eventType: string, payload: Record<string, unknown>): string {
  switch (eventType) {
    case "TOOL_CALL_STARTED":
    case "TOOL_CALL_COMPLETED":
      return "var(--running)";
    case "TOOL_CALL_FAILED":
    case "AGENT_ERROR":
      return "var(--failed)";
    case "TOOL_CALL_BLOCKED":
    case "AGENT_STOPPED":
      return "var(--cancelled)";
    case "MODEL_CALL":
      return payload.status === "error" ? "var(--failed)" : "var(--accent-soft)";
    case "SESSION_ENDED":
      return payload.status === "failed"
        ? "var(--failed)"
        : payload.status === "cancelled"
          ? "var(--cancelled)"
          : "var(--success)";
    case "TASK_COMPLETED":
      return "var(--success)";
    case "SESSION_PAUSED":
    case "SESSION_RESUMED":
    case "SUPERVISOR_DECISION":
    case "APPROVAL_REQUESTED":
    case "APPROVAL_RESOLVED":
      return "var(--warning)";
    case "PROMPT_SUBMITTED":
    case "TASK_CREATED":
      return "var(--text)";
    case "AGENT_HANDOFF":
    case "SUBAGENT_STARTED":
    case "SUBAGENT_COMPLETED":
      return "var(--accent-soft)";
    case "GUARDRAIL_RESULT":
      return payload.triggered ? "var(--failed)" : "var(--text-muted)";
    case "LOG":
    case "NOTIFICATION":
      return "var(--text-faint)";
    default:
      return "var(--text-muted)";
  }
}

const text = (value: unknown): string =>
  value === null || value === undefined ? "" : String(value);

function toolTarget(p: Record<string, unknown>): string {
  const input = p.tool_input;
  if (input && typeof input === "object") {
    const i = input as Record<string, unknown>;
    const v = i.command ?? i.file_path ?? i.path ?? i.url ?? i.pattern ?? i.query;
    if (v !== undefined) return ` · ${text(v).slice(0, 140)}`;
  }
  return "";
}

/** One-line human summary of an event, built only from fields it carries. */
function summarise(event: TraceEvent): string {
  const p = event.payload ?? {};
  switch (event.event_type) {
    case "SESSION_STARTED":
      return [text(p.source), text(p.model ?? (p.context as Record<string, unknown> | undefined)?.model)]
        .filter(Boolean)
        .join(" · ");
    case "SESSION_ENDED":
      return `${text(p.status)}${p.reason ? ` · ${text(p.reason)}` : ""}${p.error ? ` · ${text(p.error)}` : ""}`;
    case "PROMPT_SUBMITTED":
      return text(p.prompt) || (p.prompt_chars ? `${text(p.prompt_chars)} chars (not captured)` : "");
    case "TASK_CREATED":
    case "TASK_UPDATED":
      return text(p.objective);
    case "TASK_COMPLETED":
      return text(p.output);
    case "TOOL_CALL_STARTED":
      return `${text(p.tool_name)}${toolTarget(p)}`;
    case "TOOL_CALL_COMPLETED":
      return `${text(p.tool_name)}${p.duration_ms ? ` · ${text(p.duration_ms)}ms` : ""}`;
    case "TOOL_CALL_FAILED":
      return `${text(p.tool_name)} · ${text(p.error_type)}: ${text(p.error)}`;
    case "TOOL_CALL_BLOCKED":
      return `${text(p.tool_name)} not run${p.by ? ` (${text(p.by)})` : ""}${p.reason ? ` · ${text(p.reason)}` : ""}`;
    case "MODEL_CALL":
      return `${text(p.model)} · ${text(p.prompt_tokens)} in / ${text(p.completion_tokens)} out${
        p.status === "error" ? ` · ${text(p.error)}` : ""
      }`;
    case "SUBAGENT_STARTED":
    case "SUBAGENT_COMPLETED":
      return text(p.subagent_type) || text(p.subagent_id);
    case "AGENT_HANDOFF":
      return `${text(p.from_agent)} → ${text(p.to_agent)}`;
    case "AGENT_ERROR":
      return `${text(p.error_type)}: ${text(p.error)}`.replace(/^: /, "");
    case "AGENT_STOPPED":
      return text(p.reason);
    case "SESSION_PAUSED":
      return `suspended at ${text(p.checkpoint)}`;
    case "SESSION_RESUMED":
      return `resumed at ${text(p.checkpoint)}`;
    case "SUPERVISOR_DECISION":
      return `${text(p.action)}${p.intended_action ? ` (wanted ${text(p.intended_action)})` : ""} · ${text(p.rule)} · ${text(p.reason)}`;
    case "APPROVAL_REQUESTED":
      return `${text(p.tool_name)} · ${text(p.reason ?? p.source)}`;
    case "APPROVAL_RESOLVED":
      return `${text(p.status)} by ${text(p.decided_by)}${p.note ? ` · ${text(p.note)}` : ""}`;
    case "GUARDRAIL_RESULT":
      return `${text(p.guardrail)} · ${p.triggered ? "tripped" : "passed"}`;
    case "NOTIFICATION":
    case "LOG":
      return text(p.message);
    default:
      return "";
  }
}

const LABELS: Record<string, string> = {
  SESSION_STARTED: "session started",
  SESSION_ENDED: "session ended",
  PROMPT_SUBMITTED: "prompt",
  TASK_CREATED: "task",
  TASK_UPDATED: "task updated",
  TASK_COMPLETED: "task done",
  TOOL_CALL_STARTED: "tool call",
  TOOL_CALL_COMPLETED: "tool result",
  TOOL_CALL_FAILED: "tool failed",
  TOOL_CALL_BLOCKED: "not run",
  MODEL_CALL: "model call",
  SUBAGENT_STARTED: "subagent",
  SUBAGENT_COMPLETED: "subagent done",
  AGENT_HANDOFF: "handoff",
  AGENT_ERROR: "error",
  AGENT_STOPPED: "stopped",
  SESSION_PAUSED: "paused",
  SESSION_RESUMED: "resumed",
  SUPERVISOR_DECISION: "supervisor",
  APPROVAL_REQUESTED: "approval asked",
  APPROVAL_RESOLVED: "approval",
  GUARDRAIL_RESULT: "guardrail",
  NOTIFICATION: "notice",
  LOG: "log",
};

function EventRow({ event, isNew }: { event: TraceEvent; isNew?: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = toneFor(event.event_type, event.payload ?? {});
  const hasPayload = Object.keys(event.payload ?? {}).length > 0;

  return (
    <li className={cn("border-b border-line last:border-0", isNew && "row-in")}>
      <button
        type="button"
        onClick={() => hasPayload && setOpen(!open)}
        className={cn(
          "flex w-full items-start gap-3 px-4 py-2 text-left",
          hasPayload && "hover:bg-panel-raised",
        )}
        aria-expanded={open}
      >
        <ChevronRight
          className={cn(
            "mt-0.5 size-3.5 shrink-0 text-faint transition-transform",
            open && "rotate-90",
            !hasPayload && "invisible",
          )}
        />
        <Mono className="w-[86px] shrink-0 pt-px text-faint">
          {preciseTime(event.timestamp)}
        </Mono>
        <span
          className="w-[130px] shrink-0 truncate pt-px font-mono text-xs"
          style={{ color: tone }}
        >
          {LABELS[event.event_type] ?? event.event_type}{event.actor ? ` · ${event.actor}` : ""}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted">
          {summarise(event)}
        </span>
        <Mono className="shrink-0 text-faint">#{event.seq}</Mono>
      </button>
      {open ? (
        <pre className="overflow-x-auto border-t border-line bg-[#0b0b0e] px-4 py-3 font-mono text-[11px] leading-relaxed text-muted">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      ) : null}
    </li>
  );
}

export function EventTimeline({
  events,
  newestFirst = false,
  highlightAfterSeq,
  emptyLabel = "No events recorded yet.",
}: {
  events: TraceEvent[];
  newestFirst?: boolean;
  highlightAfterSeq?: number;
  emptyLabel?: string;
}) {
  if (!events.length) {
    return <div className="px-4 py-10 text-center text-xs text-faint">{emptyLabel}</div>;
  }
  const ordered = newestFirst ? [...events].reverse() : events;
  return (
    <ul className="divide-y divide-[var(--border)]">
      {ordered.map((event) => (
        <EventRow
          key={event.id}
          event={event}
          isNew={highlightAfterSeq !== undefined && event.seq > highlightAfterSeq}
        />
      ))}
    </ul>
  );
}
