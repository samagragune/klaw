"use client";

import { useRef, useState } from "react";
import { Send } from "lucide-react";
import { api, type ChatResponse } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { PageBody, PageHeader } from "@/components/shell";
import { Badge, Button, Card, CardHeader, Input, Mono } from "@/components/ui";

const SUGGESTIONS = [
  "Why did my research agent fail?",
  "Which agent is consuming the most tokens?",
  "Show me all executions that entered a loop.",
  "Why did the supervisor pause my agent?",
  "Suggest improvements to my execution policy.",
];

interface Turn {
  role: "you" | "supervisor";
  text: string;
  meta?: ChatResponse;
}

export default function SupervisorPage() {
  const config = useAsync(() => api.config(), []);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const configured = config.data?.llm_evaluator.configured ?? false;

  const ask = async (question: string) => {
    const message = question.trim();
    if (!message || busy) return;
    setTurns((current) => [...current, { role: "you", text: message }]);
    setInput("");
    setBusy(true);
    try {
      const response = await api.chat(message);
      setTurns((current) => [
        ...current,
        { role: "supervisor", text: response.answer, meta: response },
      ]);
    } catch (err) {
      setTurns((current) => [
        ...current,
        {
          role: "supervisor",
          text: err instanceof Error ? err.message : "The supervisor could not answer.",
        },
      ]);
    } finally {
      setBusy(false);
      requestAnimationFrame(() =>
        endRef.current?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  };

  return (
    <>
      <PageHeader
        title="Supervisor"
        description="Ask about your agents. Answers are grounded in stored telemetry from this workspace."
        actions={
          <Badge>
            {configured
              ? `evaluator: ${config.data?.llm_evaluator.provider}/${config.data?.llm_evaluator.model}`
              : "evaluator not configured"}
          </Badge>
        }
      />
      <PageBody>
        {!configured ? (
          <Card className="border-[#4a3a16] bg-[#17130a] px-4 py-3">
            <p className="text-xs leading-relaxed text-[var(--warning)]">
              No supervisor LLM provider is configured, so questions cannot be answered
              in natural language. Set <Mono>SUPERVISOR_LLM_PROVIDER</Mono> and{" "}
              <Mono>SUPERVISOR_LLM_API_KEY</Mono> in <Mono>.env</Mono> and restart the
              backend. Deterministic detection, incidents and execution control are
              unaffected and need no key.
            </p>
          </Card>
        ) : null}

        <Card className="flex min-h-[460px] flex-col">
          <CardHeader
            title="Conversation"
            description="Every answer cites the executions, incidents and interventions it drew on."
          />

          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {turns.length === 0 ? (
              <div className="space-y-3 py-8 text-center">
                <p className="text-sm text-muted">Ask the supervisor a question.</p>
                <div className="flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => ask(suggestion)}
                      className="rounded-md border border-line-strong bg-panel-raised px-2.5 py-1 text-xs text-muted transition-colors hover:text-ink"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              turns.map((turn, index) => (
                <div
                  key={index}
                  className={turn.role === "you" ? "flex justify-end" : "flex justify-start"}
                >
                  <div
                    className={
                      turn.role === "you"
                        ? "max-w-[80%] rounded-lg bg-[var(--accent)]/15 px-3 py-2 text-sm text-ink"
                        : "max-w-[85%] rounded-lg border border-line bg-panel-raised px-3 py-2"
                    }
                  >
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">
                      {turn.text}
                    </p>
                    {turn.meta ? (
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-line pt-2 text-[10px] text-faint">
                        <span>
                          grounded in {turn.meta.grounded_in.executions} executions,{" "}
                          {turn.meta.grounded_in.incidents} incidents,{" "}
                          {turn.meta.grounded_in.interventions} interventions (last{" "}
                          {turn.meta.grounded_in.window_days} days)
                        </span>
                        {turn.meta.evaluator ? <span>· {turn.meta.evaluator}</span> : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))
            )}
            {busy ? (
              <div className="text-xs text-faint">
                <span className="pulse">Reading execution history…</span>
              </div>
            ) : null}
            <div ref={endRef} />
          </div>

          <form
            className="flex items-center gap-2 border-t border-line p-3"
            onSubmit={(event) => {
              event.preventDefault();
              ask(input);
            }}
          >
            <Input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Ask about an agent, an execution, or an incident…"
            />
            <Button type="submit" variant="primary" disabled={busy || !input.trim()}>
              <Send className="size-3.5" /> Ask
            </Button>
          </form>
        </Card>
      </PageBody>
    </>
  );
}
