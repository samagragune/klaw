"use client";

import type { ReactNode } from "react";
import { PLATFORM_LABELS } from "@/lib/api";

const PLATFORM_TONES: Record<string, string> = {
  claude_code: "#d97757",
  openai_agents: "#10a37f",
  custom_sdk: "var(--accent-soft)",
};

export function PlatformBadge({ platform }: { platform: string | null | undefined }) {
  const key = platform ?? "custom_sdk";
  const color = PLATFORM_TONES[key] ?? "var(--idle)";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] font-medium"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} />
      {PLATFORM_LABELS[key] ?? key}
    </span>
  );
}

const ACTION_TONES: Record<string, string> = {
  ALLOW: "var(--success)",
  WARN: "var(--warning)",
  REQUEST_APPROVAL: "var(--warning)",
  BLOCK: "var(--failed)",
  STOP: "var(--failed)",
  PAUSE: "var(--paused)",
  REDIRECT: "var(--running)",
  RETRY: "var(--running)",
  ESCALATE_TO_HUMAN: "var(--warning)",
};

export function ActionBadge({ action }: { action: string }) {
  const color = ACTION_TONES[action] ?? "var(--idle)";
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wide"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      {action.replace(/_/g, " ")}
    </span>
  );
}

export function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) {
    return <span className="text-xs text-faint">not enough data</span>;
  }
  const color = score >= 85 ? "var(--success)" : score >= 65 ? "var(--warning)" : "var(--failed)";
  return (
    <span className="font-mono text-sm font-semibold tabular-nums" style={{ color }}>
      {score.toFixed(0)}
    </span>
  );
}

export function Pre({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-bg p-2 font-mono text-[11px] text-muted">
      {children}
    </pre>
  );
}
