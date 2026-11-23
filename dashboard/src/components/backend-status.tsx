"use client";

import { useEffect, useState } from "react";
import { NOT_HOSTED, getApiUrl, isPreview } from "@/lib/api";

type State = "ok" | "waking" | "down";

/**
 * Tells the operator when the supervisor backend is slow or unreachable
 * (stopped, the machine running it is off, a tunnel restarted), instead of
 * letting pages look broken. Renders nothing while it answers promptly.
 */
export function BackendStatus() {
  const [state, setState] = useState<State>("ok");

  useEffect(() => {
    if (isPreview()) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const check = async () => {
      const slow = setTimeout(() => !cancelled && setState("waking"), 2500);
      try {
        const response = await fetch(`${getApiUrl()}/health`, { cache: "no-store" });
        if (!cancelled) setState(response.ok ? "ok" : "down");
      } catch {
        if (!cancelled) setState("down");
      } finally {
        clearTimeout(slow);
        if (!cancelled) timer = setTimeout(check, 30000);
      }
    };
    check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  if (isPreview()) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
        <span className="size-1.5 rounded-full bg-[var(--accent)]" />
        <span>
          Preview - {NOT_HOSTED}. Run <code className="text-ink">klaw-supervisor start</code> on your machine, then
        </span>
        <a href="/login" className="text-ink underline">sign in</a>
        <span>to see your agents.</span>
      </div>
    );
  }
  if (state === "ok") return null;
  return (
    <div
      role="status"
      className="flex items-center gap-2 border-b px-4 py-2 text-xs"
      style={{
        color: state === "waking" ? "var(--warning)" : "var(--failed)",
        borderColor: "var(--border)",
        backgroundColor: state === "waking" ? "color-mix(in srgb, var(--warning) 8%, transparent)" : "color-mix(in srgb, var(--failed) 8%, transparent)",
      }}
    >
      <span className="pulse size-1.5 rounded-full bg-current" />
      {state === "waking"
        ? "The supervisor is responding slowly…"
        : `Can't reach the supervisor at ${getApiUrl()}. Is it running? Retrying every 30 seconds — agents keep working and queue their events.`}
    </div>
  );
}
