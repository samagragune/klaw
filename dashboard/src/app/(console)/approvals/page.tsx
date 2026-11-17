"use client";

import Link from "next/link";
import { useState } from "react";
import { api, type Approval } from "@/lib/api";
import { useAsync, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import { Pre } from "@/components/platform";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Input, Loading, Mono } from "@/components/ui";

function secondsLeft(iso: string): number {
  return Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 1000));
}

function PendingCard({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const left = secondsLeft(approval.expires_at);

  const decide = async (approve: boolean) => {
    setBusy(approve ? "approve" : "deny");
    setError(null);
    try {
      await api.decideApproval(approval.id, approve, note || undefined);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      onDone();
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink">{approval.tool_name ?? "action"}</span>
            <Badge>waiting {relativeTime(approval.created_at)}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted">{approval.reason}</p>
        </div>
        <div className="text-right">
          <div
            className="font-mono text-lg tabular-nums"
            style={{ color: left < 20 ? "var(--failed)" : "var(--warning)" }}
          >
            {left}s
          </div>
          <div className="text-[10px] text-faint">then {approval.on_timeout}</div>
        </div>
      </div>
      <div className="mt-3">
        <Pre>{JSON.stringify(approval.action_preview, null, 2)}</Pre>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          className="min-w-48 flex-1"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note to the agent (optional)"
        />
        <Button variant="primary" disabled={busy !== null} onClick={() => decide(true)}>
          {busy === "approve" ? "Approving…" : "Approve"}
        </Button>
        <Button disabled={busy !== null} onClick={() => decide(false)}>
          {busy === "deny" ? "Denying…" : "Deny"}
        </Button>
        <Link href={`/executions/${approval.execution_id}`} className="text-xs text-muted hover:text-ink">
          session →
        </Link>
      </div>
      {error ? <p className="mt-2 text-xs text-[var(--failed)]">{error}</p> : null}
    </Card>
  );
}

export default function ApprovalsPage() {
  useTicker(1000);
  const pending = useAsync(() => api.approvals({ status: "pending" }), [], { intervalMs: 4000 });
  const history = useAsync(() => api.approvals({}), [], { intervalMs: 15000 });
  useLiveMessages((m) => {
    if (m.type === "decision.created" || m.type === "approval.updated") {
      pending.refresh();
      history.refresh();
    }
  });
  const refresh = () => {
    pending.refresh();
    history.refresh();
  };
  const open = (pending.data ?? []).filter((a) => secondsLeft(a.expires_at) > 0);

  return (
    <>
      <PageHeader
        title="Human approvals"
        description="Agents waiting on a person. The agent is held at its decision point until you answer or the request expires, then the policy's timeout action applies."
      />
      <PageBody>
        {pending.error ? <ErrorState error={pending.error} retry={pending.refresh} /> : null}
        {pending.loading && !pending.data ? (
          <Loading />
        ) : open.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {open.map((a) => (
              <PendingCard key={a.id} approval={a} onDone={refresh} />
            ))}
          </div>
        ) : (
          <Card>
            <EmptyState
              title="Nothing is waiting on you"
              description="Approval requests appear here when a policy rule or an agent platform asks for a human decision."
            />
          </Card>
        )}

        <Card>
          <CardHeader title="Recent decisions" />
          <ul className="divide-y divide-[var(--border)]">
            {(history.data ?? [])
              .filter((a) => a.status !== "pending")
              .slice(0, 30)
              .map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-xs">
                  <span
                    className="w-16 font-medium capitalize"
                    style={{
                      color:
                        a.status === "approved"
                          ? "var(--success)"
                          : a.status === "denied"
                            ? "var(--failed)"
                            : "var(--idle)",
                    }}
                  >
                    {a.status}
                  </span>
                  <Mono>{a.tool_name}</Mono>
                  <span className="min-w-0 flex-1 truncate text-muted">{a.reason}</span>
                  <span className="text-faint">{a.decided_by}</span>
                  <span className="text-faint">{relativeTime(a.decided_at ?? a.created_at)}</span>
                </li>
              ))}
          </ul>
        </Card>
      </PageBody>
    </>
  );
}
