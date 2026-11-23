"use client";

import { useState } from "react";
import {
  api,
  NOT_HOSTED,
  type AgentReport,
  type AgentReview,
} from "@/lib/api";
import { useAsync, useHosted } from "@/lib/hooks";
import { relativeTime } from "@/lib/format";
import { Button, Card, CardHeader, EmptyState, ErrorState, Loading, NotHosted } from "./ui";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--failed)",
  high: "var(--failed)",
  medium: "var(--warning)",
  low: "var(--muted)",
};

function List({ title, items, tone }: { title: string; items?: string[] | null; tone?: string }) {
  if (!items?.length) return null;
  return (
    <div>
      <div className="label-mono text-[10px] text-faint">{title}</div>
      <ul className="mt-1 space-y-0.5 text-xs" style={tone ? { color: tone } : undefined}>
        {items.map((x, i) => (
          <li key={i} className={tone ? "" : "text-ink"}>- {x}</li>
        ))}
      </ul>
    </div>
  );
}

/** Everything the Klaw agent knows and did about one session, and its actions. */
export function AgentSessionPanel({ executionId }: { executionId: string }) {
  const hosted = useHosted();
  const view = useAsync(() => api.agentSession(executionId), [executionId], { intervalMs: 10000 });
  const points = useAsync(() => api.agentCheckpoints(executionId), [executionId], { intervalMs: 20000 });
  const [review, setReview] = useState<AgentReview | null>(null);
  const [report, setReport] = useState<AgentReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(null);
    }
  };

  if (!hosted) {
    return (
      <Card>
        <CardHeader title="Klaw agent" />
        <NotHosted />
      </Card>
    );
  }

  const data = view.data;
  const plan = data ? Object.values(data.plans).at(-1) : null;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader
          title="The agent's plan"
          description="What it decided 'done' means for this task, and how it will verify it."
        />
        {view.error ? (
          <ErrorState error={view.error} />
        ) : !data ? (
          <Loading />
        ) : !plan ? (
          <EmptyState title="No plan yet" description="The agent plans each task when it starts (needs a model)." />
        ) : (
          <div className="space-y-3 p-4">
            <List title="Requirements" items={plan.requirements} />
            <List title="How it will verify" items={plan.acceptance_checks} />
            <List title="Risks" items={plan.risks} tone="var(--warning)" />
            {data.inbox.length ? (
              <List title="Waiting for the agent's next step" items={data.inbox.map((i) => i.text)} tone="var(--accent)" />
            ) : null}
            {data.memory.length ? <List title="Its notes on this session" items={data.memory} /> : null}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Its investigations"
          description="Each time it worked something out with its tools: planning, verifying, monitoring, chat."
        />
        {!data ? (
          <Loading />
        ) : data.investigations.length === 0 ? (
          <EmptyState title="No investigations yet" />
        ) : (
          <ul className="max-h-[380px] divide-y divide-line overflow-y-auto">
            {[...data.investigations].reverse().map((inv, i) => (
              <li key={i} className="px-4 py-3 text-xs">
                <div className="flex items-center gap-2">
                  <span className="label-mono text-[10px] text-[var(--accent)]">{inv.purpose}</span>
                  <span className="text-faint">
                    {relativeTime(inv.at)} · {inv.steps.length} tool calls · {inv.model_calls} model calls · {inv.seconds}s
                  </span>
                </div>
                <ol className="mt-1.5 space-y-0.5">
                  {inv.steps.map((s, j) => (
                    <li key={j} className="truncate text-muted">
                      <code className="text-ink">{s.tool}</code> {JSON.stringify(s.args).slice(0, 60)} →{" "}
                      {s.result.split("\n")[0].slice(0, 90)}
                    </li>
                  ))}
                </ol>
                {typeof inv.conclusion?.verdict === "string" ? (
                  <p className="mt-1.5 text-ink">Verdict: {String(inv.conclusion.verdict)}</p>
                ) : null}
                {inv.error ? <p className="mt-1 text-[var(--warning)]">{inv.error}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Code review"
          description="The agent reviews what this session changed and lists real findings."
          action={
            <Button size="sm" disabled={busy !== null}
              onClick={() => run("review", async () => setReview(await api.agentReview(executionId)))}>
              {busy === "review" ? "Reviewing…" : "Review changes"}
            </Button>
          }
        />
        {busy === "review" ? (
          <Loading label="Reading the diff and the code" />
        ) : !review ? (
          <EmptyState title="Not reviewed yet" />
        ) : (
          <div className="space-y-2 p-4 text-xs">
            <p className="text-sm text-ink">{review.summary}</p>
            {review.findings.map((f, i) => (
              <div key={i} className="border-l-2 pl-2" style={{ borderColor: SEVERITY_COLOR[f.severity] ?? "var(--line)" }}>
                <span className="label-mono text-[10px]" style={{ color: SEVERITY_COLOR[f.severity] }}>{f.severity}</span>{" "}
                {f.file ? <code className="text-muted">{f.file}{f.line ? `:${f.line}` : ""}</code> : null}
                <p className="text-ink">{f.issue}</p>
                {f.suggestion ? <p className="text-muted">Fix: {f.suggestion}</p> : null}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Handover report"
          description="Asked, done, left, risks. Written automatically when the session ends."
          action={
            <Button size="sm" disabled={busy !== null}
              onClick={() => run("report", async () => setReport(await api.agentReport(executionId)))}>
              {busy === "report" ? "Writing…" : "Write report"}
            </Button>
          }
        />
        {busy === "report" ? (
          <Loading label="Writing the report" />
        ) : !report ? (
          <EmptyState title="No report yet" />
        ) : (
          <div className="space-y-3 p-4">
            <p className="text-sm text-ink">{report.summary}</p>
            <List title="Done" items={report.done} />
            <List title="Not done" items={report.not_done} tone="var(--warning)" />
            <List title="Files changed" items={report.files_changed} />
            <List title="Risks" items={report.risks} tone="var(--failed)" />
            <List title="Next steps" items={report.follow_ups} />
          </div>
        )}
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader
          title="Checkpoints and undo"
          description="A snapshot of the project when each task started. Restoring puts tracked files back; the restore is itself undoable."
        />
        {points.error ? (
          <ErrorState error={points.error} />
        ) : !points.data ? (
          <Loading />
        ) : points.data.checkpoints.length === 0 ? (
          <EmptyState title="No checkpoints" description="Taken when a task starts, for sessions in a git project on this machine." />
        ) : (
          <div className="grid gap-4 p-4 md:grid-cols-2">
            <ul className="space-y-2 text-xs">
              {[...points.data.checkpoints].reverse().map((c) => (
                <li key={c.sha} className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-ink">{c.label}</p>
                    <p className="text-faint">{relativeTime(c.at)} · <code>{c.sha.slice(0, 10)}</code></p>
                  </div>
                  <Button size="sm" variant="danger" disabled={busy !== null}
                    onClick={() => {
                      if (!window.confirm(`Put the project's tracked files back to "${c.label}"? Current changes are checkpointed first.`)) return;
                      run("restore", async () => {
                        const r = await api.agentRestore(executionId, c.sha);
                        setMessage(`Restored. Undo with checkpoint ${r.undo_checkpoint?.slice(0, 10) ?? "-"}.`);
                        points.refresh();
                      });
                    }}>
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap border border-line bg-bg p-3 text-[11px] text-muted">
              {points.data.changes_since_latest || "No changes since the latest checkpoint."}
            </pre>
          </div>
        )}
        {message ? <p className="px-4 pb-4 text-xs text-[var(--accent)]">{message}</p> : null}
      </Card>
    </div>
  );
}

export { NOT_HOSTED };
