"use client";

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ReactNode } from "react";
import type { AgentStatus, ExecutionStatus, Severity } from "@/lib/api";
import { NOT_HOSTED, isPreview } from "@/lib/api";
import { useHosted } from "@/lib/hooks";
import { ClawSpinner } from "./claw";

/** Shown wherever a value would be when no supervisor/model is running. */
export function NotHosted({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return <span className="label-mono text-[11px] text-faint">{NOT_HOSTED}</span>;
  }
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <span className="label-mono text-[11px] text-[var(--accent)]">{NOT_HOSTED}</span>
      <p className="max-w-md text-xs leading-relaxed text-muted">
        {isPreview()
          ? "You are looking around without a supervisor. Start one on your machine with "
          : "No supervisor is answering at the configured address. Start it with "}
        <code className="text-ink">klaw-supervisor start</code>
        {isPreview() ? ", then sign in to see your agents here." : "; this page fills in on its own."}
      </p>
    </div>
  );
}

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

/* ---------------------------------------------------------------- surfaces */

export function Card({
  children,
  className,
  ...rest
}: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("glass", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-sm font-medium text-ink">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-xs text-muted">{description}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "warning" | "danger" | "success";
}) {
  const hosted = useHosted();
  const toneClass = {
    default: "text-ink",
    warning: "text-[var(--warning)]",
    danger: "text-[var(--failed)]",
    success: "text-[var(--success)]",
  }[tone];
  return (
    <div className="glass rise-in px-5 py-4">
      <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
      <div className={cn("mt-1.5 text-[28px] font-semibold tracking-[-0.02em] tabular-nums", toneClass)}>
        {hosted ? value : <NotHosted compact />}
      </div>
      {hint && hosted ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- controls */

type ButtonVariant = "default" | "primary" | "danger" | "ghost";

export function Button({
  children,
  variant = "default",
  size = "md",
  className,
  ...rest
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants: Record<ButtonVariant, string> = {
    default:
      "press border border-[rgba(241,233,222,0.15)] bg-[rgba(241,233,222,0.05)] text-ink hover:bg-[rgba(241,233,222,0.1)]",
    primary:
      "press border border-[#f1e9de] bg-[#f1e9de] text-[#141118] hover:bg-white",
    danger:
      "press border border-[rgba(255,106,92,0.3)] bg-[rgba(255,106,92,0.08)] text-[var(--failed)] hover:bg-[rgba(255,106,92,0.15)]",
    ghost: "press border border-transparent text-muted hover:bg-[rgba(241,233,222,0.06)] hover:text-ink",
  };
  return (
    <button
      className={cn(
        "relative inline-flex items-center gap-1.5 overflow-hidden rounded-[10px] font-medium transition-all duration-200",
        "disabled:cursor-not-allowed disabled:opacity-40",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm",
        variants[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Input({
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-lg border border-[rgba(241,233,222,0.14)] bg-[#0f0d13] px-3 py-2 text-sm text-ink outline-none transition placeholder:text-faint focus:border-[var(--accent)] focus:ring-4 focus:ring-[rgba(255,106,77,0.15)]",
        "placeholder:text-faint focus:border-[var(--accent)] focus:outline-none",
        className,
      )}
      {...rest}
    />
  );
}

export function Select({
  className,
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "rounded-lg border border-[rgba(241,233,222,0.14)] bg-[#0f0d13] px-2.5 py-2 text-sm text-ink outline-none focus:border-[var(--accent)]",
        "focus:border-[var(--accent)] focus:outline-none",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
}

/* ------------------------------------------------------------------ status */

const EXECUTION_TONES: Record<ExecutionStatus, string> = {
  running: "var(--running)",
  paused: "var(--paused)",
  completed: "var(--success)",
  failed: "var(--failed)",
  cancelled: "var(--cancelled)",
  idle: "var(--idle)",
  inactive: "var(--idle)",
};

const AGENT_TONES: Record<AgentStatus, string> = {
  running: "var(--running)",
  idle: "var(--idle)",
  warning: "var(--warning)",
  failed: "var(--failed)",
  paused: "var(--paused)",
};

/* critical and high must not be the same red, or triage by colour is useless. */
export const SEVERITY_TONES: Record<Severity, string> = {
  critical: "var(--critical)",
  high: "var(--serious)",
  medium: "var(--warning)",
  low: "var(--running)",
  informational: "var(--idle)",
};

function Dot({ color, live }: { color: string; live?: boolean }) {
  return (
    <span
      className={cn("inline-block size-1.5 rounded-full", live && "pulse")}
      style={{ backgroundColor: color }}
    />
  );
}

export function StatusBadge({
  status,
  kind = "execution",
}: {
  status: ExecutionStatus | AgentStatus;
  kind?: "execution" | "agent";
}) {
  const color =
    kind === "execution"
      ? (EXECUTION_TONES[status as ExecutionStatus] ?? "var(--idle)")
      : (AGENT_TONES[status as AgentStatus] ?? "var(--idle)");
  const live = status === "running";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize"
      style={{ color, borderColor: `${color}44`, backgroundColor: `${color}14` }}
    >
      <Dot color={color} live={live} />
      {status}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const color = SEVERITY_TONES[severity] ?? "var(--idle)";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide"
      style={{ color, backgroundColor: `${color}1a` }}
    >
      {severity}
    </span>
  );
}

/**
 * Interventions are the one place the UI must never overstate itself:
 * "applied" is the only status that means the runtime actually did it.
 */
export function InterventionBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; label: string }> = {
    pending: { color: "var(--warning)", label: "pending" },
    acknowledged: { color: "var(--running)", label: "acknowledged" },
    applied: { color: "var(--success)", label: "applied" },
    failed: { color: "var(--failed)", label: "failed" },
    expired: { color: "var(--idle)", label: "expired" },
  };
  const { color, label } = map[status] ?? { color: "var(--idle)", label: status };
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] font-medium"
      style={{ color, backgroundColor: `${color}1a` }}
    >
      {label}
    </span>
  );
}

export function Badge({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border border-line-strong bg-panel-raised px-1.5 py-0.5 text-[11px] text-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ tables */

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <th
      className={cn(
        "border-b border-line px-4 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-faint",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <td className={cn("border-b border-line px-4 py-2.5 align-middle", className)}>
      {children}
    </td>
  );
}

/* ------------------------------------------------------------------- state */

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  const hosted = useHosted();
  if (!hosted) return <NotHosted />;
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-sm text-ink">{title}</p>
      {description ? (
        <p className="max-w-md text-xs leading-relaxed text-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: string; retry?: () => void }) {
  if (error === NOT_HOSTED) return <NotHosted />;
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <p className="text-sm text-[var(--failed)]">{error}</p>
      {retry ? (
        <Button size="sm" onClick={retry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  const hosted = useHosted();
  if (!hosted) return <NotHosted />;
  return (
    <div className="flex items-center justify-center gap-2.5 px-6 py-12 text-xs text-faint">
      <ClawSpinner size={18} />
      <span>{label}…</span>
    </div>
  );
}

export function Mono({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("font-mono text-xs text-muted", className)}>{children}</span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

export function KeyValue({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="divide-y divide-[var(--border)]">
      {rows.map(([key, value]) => (
        <div key={key} className="flex items-start gap-4 px-4 py-2.5">
          <dt className="w-40 shrink-0 text-xs text-faint">{key}</dt>
          <dd className="min-w-0 flex-1 text-sm text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
