"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import {
  compactNumber,
  duration,
  fullNumber,
  money,
  percent,
  relativeTime,
} from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { IncidentCard } from "@/components/incident";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  Loading,
  Mono,
  Stat,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  useTicker(5000);

  const agent = useAsync(() => api.agent(id), [id], { intervalMs: 10000 });
  const executions = useAsync(() => api.executions({ agent_id: id, limit: 50 }), [id], {
    intervalMs: 10000,
  });
  const incidents = useAsync(() => api.incidents({ agent_id: id }), [id], {
    intervalMs: 15000,
  });
  const policy = useAsync(() => api.policies(), [], { intervalMs: 60000 });

  useLiveMessages((message) => {
    if (message.type === "execution.updated" || message.type === "execution.started") {
      executions.refresh();
      agent.refresh();
    }
    if (message.type === "incident.updated") incidents.refresh();
  });

  const effective =
    policy.data?.find((row) => row.agent_id === id) ??
    policy.data?.find((row) => row.agent_id === null);

  if (agent.loading && !agent.data) {
    return (
      <>
        <PageHeader title="Agent" />
        <Loading />
      </>
    );
  }
  if (agent.error || !agent.data) {
    return (
      <>
        <PageHeader title="Agent" />
        <PageBody>
          <Card>
            <ErrorState error={agent.error ?? "Agent not found"} retry={agent.refresh} />
          </Card>
        </PageBody>
      </>
    );
  }

  const data = agent.data;
  const openIncidents = incidents.data?.filter((row) => row.status !== "resolved") ?? [];

  return (
    <>
      <PageHeader
        breadcrumb={
          <Link href="/agents" className="hover:text-ink">
            Agents
          </Link>
        }
        title={data.name}
        description={data.description ?? undefined}
        actions={<StatusBadge status={data.status} kind="agent" />}
      />
      <PageBody>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Executions" value={data.execution_count} />
          <Stat
            label="Success rate"
            value={percent(data.success_rate)}
            hint={`${data.success_count} succeeded / ${data.failure_count} failed`}
          />
          <Stat
            label="Open incidents"
            value={openIncidents.length}
            tone={openIncidents.length ? "danger" : "default"}
          />
          <Stat
            label="Tokens"
            value={compactNumber(data.total_tokens)}
            hint={fullNumber(data.total_tokens)}
          />
          <Stat label="Estimated cost" value={money(data.estimated_cost)} />
        </div>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Card>
            <CardHeader
              title="Execution history"
              description="Most recent runs, newest first."
            />
            {executions.loading && !executions.data ? (
              <Loading />
            ) : !executions.data?.length ? (
              <EmptyState title="No executions recorded for this agent yet." />
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Task</Th>
                    <Th>Status</Th>
                    <Th className="text-right">Duration</Th>
                    <Th className="text-right">Tools</Th>
                    <Th className="text-right">Errors</Th>
                    <Th className="text-right">Tokens</Th>
                    <Th className="text-right">Started</Th>
                  </tr>
                </thead>
                <tbody>
                  {executions.data.map((execution) => (
                    <tr key={execution.id} className="hover:bg-panel-raised">
                      <Td>
                        <Link
                          href={`/executions/${execution.id}`}
                          className="block max-w-[280px] truncate text-ink hover:text-[var(--accent-soft)]"
                        >
                          {execution.task ?? "Untitled execution"}
                        </Link>
                      </Td>
                      <Td>
                        <StatusBadge status={execution.status} />
                      </Td>
                      <Td className="text-right tabular-nums text-muted">
                        {duration(execution.started_at, execution.completed_at)}
                      </Td>
                      <Td className="text-right tabular-nums text-muted">
                        {execution.tool_call_count}
                      </Td>
                      <Td className="text-right tabular-nums">
                        {execution.error_count ? (
                          <span className="text-[var(--failed)]">
                            {execution.error_count}
                          </span>
                        ) : (
                          <span className="text-faint">0</span>
                        )}
                      </Td>
                      <Td className="text-right tabular-nums text-muted">
                        {compactNumber(execution.token_usage)}
                      </Td>
                      <Td className="text-right text-muted">
                        {relativeTime(execution.started_at)}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <div className="space-y-5">
            <Card>
              <CardHeader title="Agent" />
              <KeyValue
                rows={[
                  ["Agent ID", <Mono key="id">{data.id}</Mono>],
                  ["Framework", data.framework],
                  ["SDK version", data.sdk_version ?? "unknown"],
                  ["Registered", relativeTime(data.created_at)],
                  ["Last seen", relativeTime(data.last_seen_at)],
                ]}
              />
            </Card>

            <Card>
              <CardHeader
                title="Active policy"
                description={
                  effective
                    ? effective.agent_id
                      ? "Agent-specific policy"
                      : "Workspace default policy"
                    : "Built-in defaults"
                }
                action={
                  <Link
                    href="/policies"
                    className="text-xs text-muted hover:text-[var(--accent-soft)]"
                  >
                    Edit
                  </Link>
                }
              />
              <KeyValue
                rows={[
                  ["Supervision mode", effective?.supervision_mode ?? "assist"],
                  [
                    "Max duration",
                    effective?.configuration.max_duration_seconds
                      ? `${effective.configuration.max_duration_seconds}s`
                      : "no limit",
                  ],
                  [
                    "Max tool calls",
                    effective?.configuration.max_tool_calls ?? "no limit",
                  ],
                  [
                    "Repeat threshold",
                    effective?.configuration.repeated_tool_call_threshold ?? "off",
                  ],
                  [
                    "Max tokens",
                    effective?.configuration.max_tokens
                      ? fullNumber(effective.configuration.max_tokens)
                      : "no limit",
                  ],
                ]}
              />
            </Card>
          </div>
        </div>

        <Card>
          <CardHeader
            title="Incidents"
            description={`${incidents.data?.length ?? 0} recorded for this agent`}
          />
          {!incidents.data?.length ? (
            <EmptyState title="No incidents raised for this agent." />
          ) : (
            <div className="space-y-3 p-4">
              {incidents.data.map((incident) => (
                <IncidentCard
                  key={incident.id}
                  incident={incident}
                  onChange={incidents.refresh}
                />
              ))}
            </div>
          )}
        </Card>
      </PageBody>
    </>
  );
}
