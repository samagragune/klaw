"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type Incident } from "@/lib/api";
import { relativeTime, titleCase } from "@/lib/format";
import { Badge, Button, Card, SeverityBadge, cn } from "./ui";

/**
 * Evidence rendering.
 *
 * Everything shown here came from stored trace rows. Scalar fields are
 * listed plainly; the `events` array is rendered as references back to the
 * exact events that triggered detection, so a claim can always be checked.
 */
function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const events = Array.isArray(evidence.events)
    ? (evidence.events as Array<Record<string, unknown>>)
    : [];
  const scalars = Object.entries(evidence).filter(
    ([key, value]) => key !== "events" && (value === null || typeof value !== "object"),
  );
  const lists = Object.entries(evidence).filter(
    ([key, value]) => key !== "events" && Array.isArray(value),
  );

  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
        {scalars.map(([key, value]) => (
          <div key={key}>
            <dt className="text-[11px] text-faint">{titleCase(key)}</dt>
            <dd className="truncate font-mono text-xs text-ink" title={String(value)}>
              {value === null ? "—" : String(value)}
            </dd>
          </div>
        ))}
      </dl>

      {lists.map(([key, value]) => (
        <div key={key}>
          <div className="text-[11px] text-faint">{titleCase(key)}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {(value as unknown[]).map((item, index) => (
              <Badge key={index}>{String(item)}</Badge>
            ))}
          </div>
        </div>
      ))}

      {events.length ? (
        <div>
          <div className="mb-1 text-[11px] text-faint">
            Trace events cited ({events.length})
          </div>
          <div className="max-h-40 overflow-y-auto rounded border border-line bg-[#0b0b0e]">
            <table className="w-full font-mono text-[11px]">
              <tbody>
                {events.map((event, index) => (
                  <tr key={index} className="border-b border-line last:border-0">
                    <td className="px-2 py-1 text-faint">#{String(event.seq)}</td>
                    <td className="px-2 py-1 text-muted">{String(event.event_type)}</td>
                    <td className="px-2 py-1 text-faint">
                      {new Date(String(event.timestamp)).toLocaleTimeString(undefined, {
                        hour12: false,
                      })}
                    </td>
                    <td className="truncate px-2 py-1 text-faint">
                      {String(event.event_id).slice(0, 8)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** One-line gist of the evidence, for the collapsed state. */
function summariseEvidence(evidence: Record<string, unknown>): string {
  const parts: string[] = [];
  if (evidence.occurrences) parts.push(`${evidence.occurrences} occurrences`);
  if (evidence.failures) parts.push(`${evidence.failures} failures`);
  if (evidence.elapsed_seconds) parts.push(`${evidence.elapsed_seconds}s elapsed`);
  if (evidence.token_usage) parts.push(`${evidence.token_usage} tokens`);
  if (evidence.estimated_cost_usd) parts.push(`$${evidence.estimated_cost_usd}`);
  const events = Array.isArray(evidence.events) ? evidence.events.length : 0;
  if (events) parts.push(`${events} trace events cited`);
  return parts.length ? parts.join(" · ") : "Evidence recorded";
}

export function IncidentCard({
  incident,
  onChange,
  showLinks = true,
  defaultExpanded = false,
  className,
}: {
  incident: Incident;
  onChange?: () => void;
  showLinks?: boolean;
  /** Lists stay scannable collapsed; detail pages open straight to evidence. */
  defaultExpanded?: boolean;
  className?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(defaultExpanded);

  const act = async (fn: () => Promise<unknown>, label: string) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      onChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className={cn("overflow-hidden", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={incident.severity} />
            <Badge>{incident.category}</Badge>
            <Badge>{incident.source}</Badge>
            {incident.status !== "open" ? <Badge>{incident.status}</Badge> : null}
          </div>
          <h3 className="mt-1.5 text-sm font-medium text-ink">{incident.title}</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">{incident.description}</p>
          {showLinks ? (
            <div className="mt-2 flex flex-wrap gap-3 text-[11px]">
              <Link
                href={`/agents/${incident.agent_id}`}
                className="text-muted hover:text-[var(--accent-soft)]"
              >
                {incident.agent_name}
              </Link>
              <Link
                href={`/executions/${incident.execution_id}`}
                className="text-muted hover:text-[var(--accent-soft)]"
              >
                view execution
              </Link>
              <span className="text-faint">{relativeTime(incident.created_at)}</span>
            </div>
          ) : null}
        </div>

        {onChange ? (
          <div className="flex shrink-0 gap-2">
            {incident.status === "open" ? (
              <Button
                size="sm"
                disabled={busy !== null}
                onClick={() => act(() => api.updateIncident(incident.id, "acknowledged"), "ack")}
              >
                Acknowledge
              </Button>
            ) : null}
            {incident.status !== "resolved" ? (
              <Button
                size="sm"
                disabled={busy !== null}
                onClick={() => act(() => api.updateIncident(incident.id, "resolved"), "resolve")}
              >
                Resolve
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {!expanded ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="flex w-full items-center justify-between px-4 py-2.5 text-left text-xs text-muted hover:bg-panel-raised"
        >
          <span>
            {summariseEvidence(incident.evidence)}
            {incident.recommended_action ? " · recommended action available" : ""}
          </span>
          <span className="text-faint">Show evidence</span>
        </button>
      ) : (
      <div className="space-y-3 px-4 py-3">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wide text-faint">
              Evidence
            </span>
            {!defaultExpanded ? (
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="text-[11px] text-faint hover:text-muted"
              >
                Hide
              </button>
            ) : null}
          </div>
          <Evidence evidence={incident.evidence} />
        </div>

        {incident.recommended_action ? (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">
              Recommended action
            </div>
            <p className="mt-1 text-xs text-muted">{incident.recommended_action}</p>
          </div>
        ) : null}

        {incident.action_taken ? (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">
              Action taken
            </div>
            <p className="mt-1 text-xs text-muted">{incident.action_taken}</p>
          </div>
        ) : null}

        <div>
          <div className="flex items-center justify-between">
            <div className="text-[11px] uppercase tracking-wide text-faint">
              Supervisor diagnosis
            </div>
            {onChange ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                onClick={() => act(() => api.diagnose(incident.id), "diagnose")}
              >
                {busy === "diagnose" ? "Analysing…" : "Run diagnosis"}
              </Button>
            ) : null}
          </div>
          {incident.diagnosis ? (
            <>
              <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-muted">
                {incident.diagnosis}
              </p>
              {incident.diagnosis_source ? (
                <p className="mt-1 font-mono text-[10px] text-faint">
                  generated by {incident.diagnosis_source}
                </p>
              ) : null}
            </>
          ) : (
            <p className="mt-1 text-xs text-faint">
              Not generated. Detection and evidence above are deterministic and need no
              model.
            </p>
          )}
        </div>

        {error ? <div className="text-[11px] text-[var(--failed)]">{error}</div> : null}
      </div>
      )}
    </Card>
  );
}
