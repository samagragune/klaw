"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { api, getApiUrl, clearToken } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { relativeTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Input,
  KeyValue,
  Loading,
  Mono,
  Table,
  Td,
  Th,
} from "@/components/ui";

const SNIPPET = `from reliability import Supervisor

supervisor = Supervisor(
    api_key="YOUR_API_KEY",      # or set RELIABILITY_API_KEY
    agent_id="research-agent",   # stable name; registration is idempotent
    api_url="${getApiUrl()}",
)

@supervisor.monitor(task="Research AI startups")
async def research():
    async with supervisor.tool("search", {"q": "AI startups"}) as call:
        call.set_result(await my_search("AI startups"))

    async with supervisor.llm("gpt-4o-mini") as call:
        response = await my_model_call()
        call.record_usage(response.prompt_tokens, response.completion_tokens)

    return "done"

result = await research()   # pause / cancel take effect at each checkpoint`;

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

export default function SettingsPage() {
  const config = useAsync(() => api.config(), []);
  const keys = useAsync(() => api.keys(), []);
  const audit = useAsync(() => api.audit(), [], { intervalMs: 30000 });

  const [keyName, setKeyName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mint = async () => {
    setError(null);
    try {
      const result = await api.createKey(keyName.trim() || "dashboard");
      setCreated(result.key);
      setKeyName("");
      keys.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create a key");
    }
  };

  return (
    <>
      <PageHeader
        title="Settings"
        description="Credentials, SDK integration and this deployment's configuration."
      />
      <PageBody>
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader title="This deployment" />
            {config.loading ? (
              <Loading />
            ) : (
              <KeyValue
                rows={[
                  ["Backend", <Mono key="url">{getApiUrl()}</Mono>],
                  ["Environment", config.data?.environment ?? "—"],
                  ["Database", config.data?.database ?? "—"],
                  ["Event bus", config.data?.event_bus ?? "—"],
                  [
                    "LLM evaluator",
                    config.data?.llm_evaluator.configured ? (
                      <Badge>
                        {config.data.llm_evaluator.provider} /{" "}
                        {config.data.llm_evaluator.model}
                      </Badge>
                    ) : (
                      <span className="text-muted">
                        not configured — deterministic detection unaffected
                      </span>
                    ),
                  ],
                  ["Signed in as", config.data?.user.email ?? "—"],
                ]}
              />
            )}
            <div className="border-t border-line px-4 py-3">
              <Button
                size="sm"
                onClick={() => {
                  clearToken();
                  window.location.reload();
                }}
              >
                Forget dashboard token
              </Button>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="API keys"
              description="Used by the SDK. Only a hash is stored — a key is shown once, at creation."
            />
            <div className="flex items-center gap-2 border-b border-line p-3">
              <Input
                placeholder="Key name (e.g. production-agents)"
                value={keyName}
                onChange={(event) => setKeyName(event.target.value)}
              />
              <Button variant="primary" onClick={mint}>
                Create
              </Button>
            </div>

            {created ? (
              <div className="border-b border-line bg-[#0b140e] px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] uppercase tracking-wide text-[var(--success)]">
                    Copy this now — it cannot be shown again
                  </span>
                  <CopyButton value={created} />
                </div>
                <Mono className="mt-1 block break-all text-ink">{created}</Mono>
              </div>
            ) : null}
            {error ? (
              <div className="border-b border-line px-4 py-2 text-xs text-[var(--failed)]">
                {error}
              </div>
            ) : null}

            {keys.loading && !keys.data ? (
              <Loading />
            ) : !keys.data?.length ? (
              <EmptyState title="No API keys yet" />
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Name</Th>
                    <Th>Prefix</Th>
                    <Th>Last used</Th>
                    <Th />
                  </tr>
                </thead>
                <tbody>
                  {keys.data.map((key) => (
                    <tr key={key.id}>
                      <Td>
                        {key.name}
                        {key.revoked_at ? (
                          <Badge className="ml-2">revoked</Badge>
                        ) : null}
                      </Td>
                      <Td>
                        <Mono>{key.prefix}…</Mono>
                      </Td>
                      <Td className="text-muted">{relativeTime(key.last_used_at)}</Td>
                      <Td className="text-right">
                        {key.revoked_at ? null : (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              await api.revokeKey(key.id);
                              keys.refresh();
                            }}
                          >
                            Revoke
                          </Button>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>

        <Card>
          <CardHeader
            title="SDK integration"
            description="pip install -e ./sdk — then wrap your entrypoint."
            action={<CopyButton value={SNIPPET} />}
          />
          <pre className="overflow-x-auto px-4 py-3 font-mono text-[11px] leading-relaxed text-muted">
            {SNIPPET}
          </pre>
          <div className="border-t border-line px-4 py-3 text-xs leading-relaxed text-muted">
            Checkpoints are what make pause and cancel real: the SDK yields to the
            control plane on entering <Mono>monitor</Mono>, before every tool call,
            before every model call, and anywhere you{" "}
            <Mono>await supervisor.checkpoint()</Mono>. An agent that never reaches a
            checkpoint cannot be paused, and the dashboard will keep showing the
            command as pending rather than claiming otherwise.
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Audit log"
            description="Every control action and credential change, newest first."
          />
          {!audit.data?.length ? (
            <EmptyState title="Nothing audited yet" />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>When</Th>
                  <Th>Actor</Th>
                  <Th>Action</Th>
                  <Th>Target</Th>
                </tr>
              </thead>
              <tbody>
                {audit.data.slice(0, 40).map((entry) => (
                  <tr key={entry.id}>
                    <Td className="text-muted">{relativeTime(entry.created_at)}</Td>
                    <Td>
                      <Badge>{entry.actor}</Badge>
                    </Td>
                    <Td className="font-mono text-xs text-ink">{entry.action}</Td>
                    <Td>
                      <Mono>
                        {entry.target_type} {entry.target_id.slice(0, 8)}
                      </Mono>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </PageBody>
    </>
  );
}
