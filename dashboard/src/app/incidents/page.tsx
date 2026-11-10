"use client";

import { useState } from "react";
import { api, type Severity } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { PageBody, PageHeader } from "@/components/shell";
import { IncidentCard } from "@/components/incident";
import {
  Card,
  EmptyState,
  ErrorState,
  Loading,
  SEVERITY_TONES,
  Select,
} from "@/components/ui";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "informational"];

export default function IncidentsPage() {
  useTicker(15000);
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");

  const incidents = useAsync(
    () => api.incidents({ status: status || undefined, severity: severity || undefined }),
    [status, severity],
    { intervalMs: 15000 },
  );

  useLiveMessages((message) => {
    if (message.type === "incident.updated") incidents.refresh();
  });

  const counts = SEVERITIES.map((level) => ({
    level,
    count: incidents.data?.filter((row) => row.severity === level).length ?? 0,
  })).filter((row) => row.count > 0);

  return (
    <>
      <PageHeader
        title="Incidents"
        description="Failures and anomalies detected from stored execution evidence."
        actions={
          <div className="flex items-center gap-2">
            <Select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="resolved">Resolved</option>
            </Select>
            <Select
              value={severity}
              onChange={(event) => setSeverity(event.target.value)}
            >
              <option value="">All severities</option>
              {SEVERITIES.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      <PageBody>
        {counts.length ? (
          <div className="flex flex-wrap items-center gap-4 rounded-lg border border-line bg-panel px-4 py-2.5">
            {counts.map(({ level, count }) => (
              <div key={level} className="flex items-center gap-1.5 text-xs">
                <span
                  className="size-2 rounded-[2px]"
                  style={{ backgroundColor: SEVERITY_TONES[level] }}
                />
                <span className="text-muted capitalize">{level}</span>
                <span className="tabular-nums text-ink">{count}</span>
              </div>
            ))}
          </div>
        ) : null}

        {incidents.loading && !incidents.data ? (
          <Loading />
        ) : incidents.error ? (
          <Card>
            <ErrorState error={incidents.error} retry={incidents.refresh} />
          </Card>
        ) : !incidents.data?.length ? (
          <Card>
            <EmptyState
              title="No incidents match these filters"
              description="Deterministic detectors raise an incident as soon as a run trips a policy limit. Try: python demo/run_demo.py loop"
            />
          </Card>
        ) : (
          <div className="space-y-3">
            {incidents.data.map((incident) => (
              <IncidentCard
                key={incident.id}
                incident={incident}
                onChange={incidents.refresh}
              />
            ))}
          </div>
        )}
      </PageBody>
    </>
  );
}
