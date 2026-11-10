"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { IncidentCard } from "@/components/incident";
import { EventTimeline } from "@/components/trace";
import {
  Card,
  CardHeader,
  ErrorState,
  KeyValue,
  Loading,
  Mono,
  StatusBadge,
} from "@/components/ui";

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const incident = useAsync(() => api.incident(id), [id], { intervalMs: 20000 });
  const execution = useAsync(
    () => api.execution(incident.data!.execution_id),
    [incident.data?.execution_id],
    { enabled: Boolean(incident.data?.execution_id) },
  );

  if (incident.loading && !incident.data) {
    return (
      <>
        <PageHeader title="Incident" />
        <Loading />
      </>
    );
  }
  if (incident.error || !incident.data) {
    return (
      <>
        <PageHeader title="Incident" />
        <PageBody>
          <Card>
            <ErrorState
              error={incident.error ?? "Incident not found"}
              retry={incident.refresh}
            />
          </Card>
        </PageBody>
      </>
    );
  }

  const data = incident.data;
  // Only the events the incident actually cites, so the trace shown here is
  // exactly the evidence behind the finding.
  const citedSeqs = new Set(
    (Array.isArray(data.evidence.events) ? data.evidence.events : []).map(
      (event) => (event as { seq: number }).seq,
    ),
  );
  const cited = execution.data?.events.filter((event) => citedSeqs.has(event.seq)) ?? [];

  return (
    <>
      <PageHeader
        breadcrumb={
          <Link href="/incidents" className="hover:text-ink">
            Incidents
          </Link>
        }
        title={data.title}
        description={<Mono>{data.id}</Mono>}
        actions={<StatusBadge status={data.execution_status} />}
      />
      <PageBody>
        <IncidentCard
          incident={data}
          onChange={incident.refresh}
          showLinks={false}
          defaultExpanded
        />

        <div className="grid gap-5 xl:grid-cols-2">
          <Card>
            <CardHeader title="Context" />
            <KeyValue
              rows={[
                [
                  "Agent",
                  <Link
                    key="agent"
                    href={`/agents/${data.agent_id}`}
                    className="text-ink hover:text-[var(--accent-soft)]"
                  >
                    {data.agent_name}
                  </Link>,
                ],
                [
                  "Execution",
                  <Link
                    key="execution"
                    href={`/executions/${data.execution_id}`}
                    className="text-ink hover:text-[var(--accent-soft)]"
                  >
                    {data.execution_task ?? data.execution_id}
                  </Link>,
                ],
                ["Execution status", data.execution_status],
                ["Category", data.category],
                ["Detected by", data.source],
                ["Raised", relativeTime(data.created_at)],
                ["Last updated", relativeTime(data.updated_at)],
              ]}
            />
          </Card>

          <Card>
            <CardHeader
              title="Cited trace events"
              description={
                cited.length
                  ? `${cited.length} events from the execution trace`
                  : "This incident cites aggregate counters rather than individual events."
              }
            />
            {execution.loading ? (
              <Loading />
            ) : (
              <div className="max-h-[420px] overflow-y-auto">
                <EventTimeline
                  events={cited}
                  emptyLabel="No individual events cited."
                />
              </div>
            )}
          </Card>
        </div>
      </PageBody>
    </>
  );
}
