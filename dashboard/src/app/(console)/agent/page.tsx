"use client";

import Link from "next/link";
import { useState } from "react";
import { api, NOT_HOSTED, type AgentChatReply } from "@/lib/api";
import { useAsync, useHosted, useTicker } from "@/lib/hooks";
import { useLiveMessages } from "@/lib/live";
import { compactNumber, percent, relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Loading,
  NotHosted,
  Stat,
} from "@/components/ui";

const ACTION_TONE: Record<string, string> = {
  ALLOW: "var(--success)",
  WARN: "var(--warning)",
  BLOCK: "var(--failed)",
  STOP: "var(--failed)",
  REQUEST_APPROVAL: "var(--warning)",
  REDIRECT: "var(--accent)",
  ESCALATE_TO_HUMAN: "var(--failed)",
};

const VERDICT_LABEL: Record<string, string> = {
  pass: "Pass",
  incomplete: "Incomplete",
  retry: "Needs another attempt",
  escalated: "Handed to a human",
};

function Tone({ action }: { action: string }) {
  const color = ACTION_TONE[action] ?? "var(--muted)";
  return (
    <span
      className="label-mono rounded px-1.5 py-0.5 text-[10px]"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      {action.replace(/_/g, " ").toLowerCase()}
    </span>
  );
}

export default function AgentPage() {
  useTicker(10000);
  const hosted = useHosted();
  const status = useAsync(() => api.agentStatus(), [], { intervalMs: 15000 });
  const feed = useAsync(() => api.agentFeed(60), [], { intervalMs: 10000 });
  const reports = useAsync(() => api.agentReports(), [], { intervalMs: 30000 });
  const effect = useAsync(() => api.agentEffectiveness(), [], { intervalMs: 60000 });
  const memory = useAsync(() => api.agentMemory(), [], { intervalMs: 60000 });

  useLiveMessages((message) => {
    const type = message.type as string;
    if (type === "decision.created" || type === "agent.assessment") feed.refresh();
  });

  const s = status.data;
  const brainReady = hosted && s?.available;
  const day = s?.last_24h;
  const modelCalls = day?.model_calls.reduce((n, m) => n + m.calls, 0) ?? 0;

  return (
    <>
      <PageHeader
        title="Klaw agent"
        description="The supervisor's own agent: it plans each task, checks every action, verifies 'done' against the real diff and tests, investigates trouble on its own, and acts."
      />
      <PageBody>
        <Card className="p-5">
          {!hosted ? (
            <NotHosted />
          ) : status.error ? (
            <ErrorState error={status.error} retry={status.refresh} />
          ) : !s ? (
            <Loading />
          ) : (
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="label-mono text-[11px] text-faint">brain</div>
                {s.available ? (
                  <div className="mt-1 text-lg text-ink">
                    {s.provider} · <span className="text-muted">{s.fast_model}</span> per action ·{" "}
                    <span className="text-muted">{s.model}</span> per task
                  </div>
                ) : (
                  <div className="mt-1">
                    <span className="label-mono text-[12px] text-[var(--accent)]">{NOT_HOSTED}</span>
                    <p className="mt-1 max-w-xl text-xs text-muted">
                      {s.reason ?? "No model is configured."} Pick one with{" "}
                      <code className="text-ink">klaw-supervisor set-brain</code> (Gemini, OpenAI,
                      Anthropic, OpenRouter, Groq, DeepSeek, a custom endpoint, or Ollama on this machine)
                      and restart. Until then the rules decide alone.
                    </p>
                  </div>
                )}
                {s.last_error ? <p className="mt-2 text-xs text-[var(--warning)]">Last model error: {s.last_error}</p> : null}
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge>{s.mode} mode</Badge>
                <Badge>{s.enabled ? "agent on" : "agent off in policy"}</Badge>
                <Badge>{s.live_sessions} live sessions</Badge>
              </div>
            </div>
          )}
        </Card>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Actions reviewed (24h)" value={brainReady ? compactNumber(day?.actions_reviewed ?? 0) : NOT_HOSTED} />
          <Stat
            label="Blocked or held"
            value={brainReady ? (day?.blocked ?? 0) + (day?.held_for_human ?? 0) + (day?.stopped ?? 0) : NOT_HOSTED}
            tone="danger"
          />
          <Stat label="Guided" value={brainReady ? day?.guided ?? 0 : NOT_HOSTED} tone="warning" />
          <Stat
            label="Tasks verified"
            value={
              brainReady
                ? (day?.completions_passed ?? 0) + (day?.completions_incomplete ?? 0) + (day?.completions_retry ?? 0)
                : NOT_HOSTED
            }
            hint={brainReady ? `${day?.completions_passed ?? 0} passed first or later` : undefined}
            tone="success"
          />
          <Stat
            label="Send-backs that then passed"
            value={brainReady ? percent(effect.data?.send_back_success_rate ?? null) : NOT_HOSTED}
            hint={effect.data ? `${effect.data.sent_back_then_passed}/${effect.data.tasks_sent_back} tasks` : undefined}
          />
          <Stat
            label="Blocks the agent accepted"
            value={brainReady ? percent(effect.data?.block_acceptance_rate ?? null) : NOT_HOSTED}
            hint={effect.data ? `${effect.data.blocks} blocks` : undefined}
          />
          <Stat label="Model calls (24h)" value={brainReady ? compactNumber(modelCalls) : NOT_HOSTED} />
          <Stat
            label="Avg model latency"
            value={
              brainReady && day?.model_calls.length
                ? `${(day.model_calls.reduce((n, m) => n + m.avg_latency_ms * m.calls, 0) / Math.max(1, modelCalls) / 1000).toFixed(1)}s`
                : NOT_HOSTED
            }
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <Chat enabled={Boolean(brainReady)} />
          </div>
          <Card className="lg:col-span-2">
            <CardHeader title="What it learned" description="Project memory: your approvals, denials and lessons. Used in every future session." />
            {memory.error ? (
              <ErrorState error={memory.error} />
            ) : !memory.data ? (
              <Loading />
            ) : memory.data.length === 0 ? (
              <EmptyState title="Nothing learned yet" description="Approve or deny something, or tell the agent a preference in the chat." />
            ) : (
              <ul className="max-h-[360px] divide-y divide-line overflow-y-auto">
                {memory.data.map((m) => (
                  <li key={m.id} className="group flex gap-3 px-4 py-2.5 text-xs">
                    <div className="min-w-0 flex-1">
                      <p className="text-ink">{m.note}</p>
                      <p className="mt-0.5 truncate text-faint">{m.project === "*" ? "all projects" : m.project}</p>
                    </div>
                    <button
                      className="text-faint opacity-0 hover:text-[var(--failed)] group-hover:opacity-100"
                      onClick={async () => {
                        await api.agentForget(m.id);
                        memory.refresh();
                      }}
                    >
                      forget
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <Card>
          <CardHeader title="What the agent is doing" description="Its decisions, verdicts and own investigations, with its reasoning." />
          {feed.error ? (
            <ErrorState error={feed.error} retry={feed.refresh} />
          ) : !feed.data ? (
            <Loading />
          ) : feed.data.length === 0 ? (
            <EmptyState
              title={brainReady ? "Nothing yet" : NOT_HOSTED}
              description="Connect an agent under Integrations; every decision appears here with the agent's reasoning."
            />
          ) : (
            <ul className="divide-y divide-line">
              {feed.data.map((item, i) => (
                <li key={`${item.at}-${i}`} className="flex gap-4 px-4 py-3">
                  <div className="w-24 shrink-0 text-[11px] text-faint">{relativeTime(item.at)}</div>
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Tone action={item.action} />
                      <span className="label-mono text-[10px] text-faint">{item.kind}</span>
                      {item.verdict ? (
                        <span className="text-ink">{VERDICT_LABEL[item.verdict] ?? item.verdict}</span>
                      ) : null}
                      {item.tool_name ? <code className="text-muted">{item.tool_name}</code> : null}
                      <Link href={`/executions/${item.execution_id}`} className="truncate text-muted hover:text-ink">
                        {item.session_task ?? "session"}
                      </Link>
                    </div>
                    {item.reasoning ? <p className="text-[13px] leading-relaxed text-ink">{item.reasoning}</p> : null}
                    {item.guidance ? (
                      <p className="border-l-2 border-[var(--accent)] pl-2 text-xs text-muted">to the agent: {item.guidance}</p>
                    ) : null}
                    {item.applied && item.applied !== "none" ? <p className="text-xs text-faint">did: {item.applied}</p> : null}
                    {item.held_back ? <p className="text-xs text-faint">held back: {item.held_back}</p> : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Session reports" description="Written by the agent when a session ends: asked, done, left, risks." />
          {reports.error ? (
            <ErrorState error={reports.error} />
          ) : !reports.data ? (
            <Loading />
          ) : reports.data.length === 0 ? (
            <EmptyState title="No reports yet" description="They appear when sessions end, or open a session and ask for one." />
          ) : (
            <div className="grid gap-3 p-4 md:grid-cols-2">
              {reports.data.map((r) => (
                <Link key={r.execution_id} href={`/executions/${r.execution_id}`} className="glass block p-4 hover:border-line-strong">
                  <div className="label-mono text-[10px] text-faint">{relativeTime(r.at)}</div>
                  <p className="mt-1 text-sm text-ink">{r.summary}</p>
                  {r.not_done?.length ? (
                    <p className="mt-2 text-xs text-[var(--warning)]">Left: {r.not_done.join("; ")}</p>
                  ) : null}
                </Link>
              ))}
            </div>
          )}
        </Card>
      </PageBody>
    </>
  );
}

function Chat({ enabled }: { enabled: boolean }) {
  const sessions = useAsync(() => api.executions({ limit: 30 }), []);
  const [executionId, setExecutionId] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ role: "you" | "agent"; text: string; reply?: AgentChatReply }[]>([]);

  const send = async () => {
    const text = message.trim();
    if (!text) return;
    setMessage("");
    setBusy(true);
    setLog((l) => [...l, { role: "you", text }]);
    try {
      const history = log.map((m) => ({ role: m.role === "you" ? "operator" : "agent", text: m.text }));
      const reply = await api.agentChat(text, executionId || undefined, history);
      setLog((l) => [...l, { role: "agent", text: reply.answer, reply }]);
    } catch (err) {
      setLog((l) => [...l, { role: "agent", text: err instanceof Error ? err.message : "Failed" }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        title="Talk to the agent"
        description="Ask about a session or tell it what to do. It looks things up with its tools and can guide, stop or remember."
        action={
          <select
            value={executionId}
            onChange={(e) => setExecutionId(e.target.value)}
            className="h-8 max-w-[220px] border border-line bg-bg px-2 text-xs text-ink"
          >
            <option value="">Most recent session</option>
            {(sessions.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {(s.task ?? s.id).slice(0, 60)}
              </option>
            ))}
          </select>
        }
      />
      <div className="flex-1 space-y-3 overflow-y-auto p-4" style={{ minHeight: 220, maxHeight: 420 }}>
        {!enabled ? (
          <NotHosted />
        ) : log.length === 0 ? (
          <p className="text-xs text-muted">
            Try: &ldquo;Did it touch anything besides what I asked?&rdquo;, &ldquo;Why did you block that?&rdquo;,
            &ldquo;Stop it&rdquo;, or &ldquo;Remember: never run migrations here.&rdquo;
          </p>
        ) : (
          log.map((m, i) => (
            <div key={i} className={m.role === "you" ? "text-right" : ""}>
              <div
                className={`inline-block max-w-[90%] px-3 py-2 text-left text-[13px] leading-relaxed ${
                  m.role === "you" ? "bg-panel-raised text-ink" : "glass text-ink"
                }`}
              >
                {m.text}
                {m.reply?.steps.length ? (
                  <div className="label-mono mt-2 text-[10px] text-faint">
                    used: {m.reply.steps.map((s) => s.tool).join(" → ")}
                  </div>
                ) : null}
                {m.reply?.effects.length ? (
                  <div className="mt-1 text-[11px] text-[var(--accent)]">
                    did: {m.reply.effects.map((e) => e.type).join(", ")}
                  </div>
                ) : null}
              </div>
            </div>
          ))
        )}
        {busy ? <Loading label="The agent is working" /> : null}
      </div>
      <form
        className="flex gap-2 border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          disabled={!enabled || busy}
          placeholder={enabled ? "Message the Klaw agent" : NOT_HOSTED}
          className="h-9 flex-1 border border-line-strong bg-bg px-3 text-sm text-ink outline-none focus:border-[var(--accent)]"
        />
        <Button variant="primary" disabled={!enabled || busy || !message.trim()} type="submit">
          Send
        </Button>
      </form>
    </Card>
  );
}
