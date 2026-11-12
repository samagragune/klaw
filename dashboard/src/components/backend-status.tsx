"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

type State = "ok" | "waking" | "down";

/**
 * Free hosting puts the backend to sleep when idle; the first request then
 * waits for a cold start. Say so, instead of letting the page look broken.
 * Renders nothing while the backend answers promptly.
 */
export function BackendStatus() {
  const [state, setState] = useState<State>("ok");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const check = async () => {
      const slow = setTimeout(() => !cancelled && setState("waking"), 2500);
      try {
        const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
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
        ? "Waking the supervisor — free hosting sleeps when idle; this takes up to a minute. Agents keep working meanwhile."
        : "Can't reach the supervisor right now. Retrying every 30 seconds; agents keep working and their events are queued."}
    </div>
  );
}
