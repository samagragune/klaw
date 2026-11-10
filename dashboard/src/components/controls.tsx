"use client";

import { useState } from "react";
import { Ban, Pause, Play } from "lucide-react";
import { api, type ExecutionDetail, type Intervention } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { Button, InterventionBadge, Mono, cn } from "./ui";

/**
 * Execution controls.
 *
 * Issuing a command only records intent. Until the runtime acknowledges, the
 * UI says "awaiting runtime" rather than claiming the agent stopped - the
 * backend cannot know, and neither can this component.
 */
export function ExecutionControls({
  execution,
  onChange,
}: {
  execution: ExecutionDetail;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const terminal = ["completed", "failed", "cancelled"].includes(execution.status);
  const awaiting = execution.interventions.filter((intervention) =>
    ["pending", "acknowledged"].includes(intervention.status),
  );

  const send = async (action: string, reason: string) => {
    setBusy(action);
    setError(null);
    try {
      await api.control(execution.id, action, reason);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Control request failed");
    } finally {
      setBusy(null);
    }
  };

  if (terminal) {
    return (
      <div className="text-xs text-faint">
        Execution is {execution.status}; it can no longer be controlled.
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        {execution.status === "paused" ? (
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null}
            onClick={() => send("resume", "Resumed from the dashboard")}
          >
            <Play className="size-3.5" /> Resume
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => send("pause", "Paused from the dashboard")}
          >
            <Pause className="size-3.5" /> Pause
          </Button>
        )}
        <Button
          size="sm"
          variant="danger"
          disabled={busy !== null}
          onClick={() => send("cancel", "Cancelled from the dashboard")}
        >
          <Ban className="size-3.5" /> Cancel
        </Button>
      </div>

      {awaiting.length > 0 ? (
        <div className="text-[11px] text-[var(--warning)]">
          {awaiting.length === 1 ? "1 command" : `${awaiting.length} commands`} sent —
          awaiting runtime acknowledgement
        </div>
      ) : null}
      {error ? <div className="text-[11px] text-[var(--failed)]">{error}</div> : null}
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  pause: "Pause",
  resume: "Resume",
  cancel: "Cancel",
  block_tool: "Block tool",
  request_approval: "Request approval",
};

export function InterventionList({
  interventions,
  className,
}: {
  interventions: Intervention[];
  className?: string;
}) {
  if (!interventions.length) {
    return (
      <div className="px-4 py-8 text-center text-xs text-faint">
        No control actions have been taken on this execution.
      </div>
    );
  }
  return (
    <ul className={cn("divide-y divide-[var(--border)]", className)}>
      {interventions.map((intervention) => (
        <li key={intervention.id} className="px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-ink">
              {ACTION_LABEL[intervention.action] ?? intervention.action}
            </span>
            <InterventionBadge status={intervention.status} />
            <span className="text-xs text-faint">
              by {intervention.requested_by} · {relativeTime(intervention.created_at)}
            </span>
          </div>
          {intervention.reason ? (
            <p className="mt-1 text-xs text-muted">{intervention.reason}</p>
          ) : null}
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-faint">
            <span>
              requested {relativeTime(intervention.created_at)}
            </span>
            <span>
              acknowledged{" "}
              {intervention.acknowledged_at
                ? relativeTime(intervention.acknowledged_at)
                : "—"}
            </span>
            <span
              className={
                intervention.applied_at ? "text-[var(--success)]" : "text-[var(--warning)]"
              }
            >
              {intervention.applied_at
                ? `runtime confirmed ${relativeTime(intervention.applied_at)}`
                : "not confirmed by the runtime"}
            </span>
          </div>
          {intervention.runtime_result?.checkpoint ? (
            <Mono className="mt-1 block">
              enforced at checkpoint {String(intervention.runtime_result.checkpoint)}
            </Mono>
          ) : null}
          {intervention.runtime_result?.note ? (
            <Mono className="mt-1 block">{String(intervention.runtime_result.note)}</Mono>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
