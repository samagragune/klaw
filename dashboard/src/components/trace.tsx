"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { TraceEvent } from "@/lib/api";
import { preciseTime } from "@/lib/format";
import { cn, Mono } from "./ui";

/** Colour by event family, so a long trace is scannable at a glance. */
function toneFor(eventType: string): string {
  if (eventType.startsWith("tool.")) {
    if (eventType === "tool.failed") return "var(--failed)";
    if (eventType === "tool.blocked") return "var(--cancelled)";
    return "var(--running)";
  }
  if (eventType.startsWith("llm.")) {
    return eventType === "llm.failed" ? "var(--failed)" : "var(--accent-soft)";
  }
  if (eventType === "execution.failed") return "var(--failed)";
  if (eventType === "execution.cancelled") return "var(--cancelled)";
  if (eventType === "execution.completed") return "var(--success)";
  if (eventType === "execution.paused" || eventType === "execution.resumed")
    return "var(--warning)";
  if (eventType.startsWith("intervention.")) return "var(--warning)";
  if (eventType === "log") return "var(--text-faint)";
  return "var(--text-muted)";
}

const text = (value: unknown): string =>
  value === null || value === undefined ? "" : String(value);

/** One-line human summary of an event, built only from fields it carries. */
function summarise(event: TraceEvent): string {
  const p = event.payload ?? {};
  switch (event.event_type) {
    case "execution.started":
      return text(p.task);
    case "execution.completed":
      return text(p.output_summary);
    case "execution.failed":
      return `${text(p.error_type)}: ${text(p.error)}`.replace(/^: /, "");
    case "execution.cancelled":
      return text(p.reason) || "cancelled by the control plane";
    case "execution.paused":
      return `suspended at checkpoint ${text(p.checkpoint)}`;
    case "execution.resumed":
      return `resumed at checkpoint ${text(p.checkpoint)}`;
    case "tool.started":
      return text(p.tool_name);
    case "tool.completed":
      return `${text(p.tool_name)} · ${text(p.duration_ms)}ms`;
    case "tool.failed":
      return `${text(p.tool_name)} · ${text(p.error_type)}: ${text(p.error)}`;
    case "tool.blocked":
      return `${text(p.tool_name)} blocked${p.reason ? ` · ${text(p.reason)}` : ""}`;
    case "llm.requested":
      return text(p.model);
    case "llm.completed":
      return `${text(p.model)} · ${text(p.prompt_tokens)} in / ${text(p.completion_tokens)} out`;
    case "llm.failed":
      return `${text(p.model)} · ${text(p.error)}`;
    case "log":
      return text(p.message);
    default:
      return "";
  }
}

function EventRow({ event, isNew }: { event: TraceEvent; isNew?: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = toneFor(event.event_type);
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
          {event.event_type}
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
