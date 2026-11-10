"use client";

import { useEffect, useState, type ReactNode } from "react";
import { API_URL, getToken, setToken } from "@/lib/api";
import { LiveProvider } from "@/lib/live";
import { Shell } from "./shell";
import { Button, Card, Input } from "./ui";

/**
 * Gate the app on having a dashboard token.
 *
 * Local development seeds one into `.env.local`; this screen is the fallback
 * for a fresh browser or a rotated token. It is deliberately not a login - a
 * real session flow replaces this component and nothing else.
 */
function TokenGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [value, setValue] = useState("");

  useEffect(() => {
    setHasToken(Boolean(getToken()));
    setReady(true);
  }, []);

  if (!ready) return null;

  if (!hasToken) {
    return (
      <div className="grid min-h-screen place-items-center p-6">
        <Card className="w-full max-w-md p-6">
          <h1 className="text-base font-semibold text-ink">Connect to Reliability</h1>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Paste the dashboard token printed by{" "}
            <code className="font-mono text-xs text-ink">python -m app.seed</code>, or
            any API key for this workspace. It is stored in this browser only.
          </p>
          <form
            className="mt-4 space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!value.trim()) return;
              setToken(value);
              setHasToken(true);
            }}
          >
            <Input
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="DEV_SESSION_TOKEN or rl_live_…"
            />
            <Button type="submit" variant="primary" className="w-full justify-center">
              Connect
            </Button>
          </form>
          <p className="mt-4 text-xs text-faint">Backend: {API_URL}</p>
        </Card>
      </div>
    );
  }

  return <Shell>{children}</Shell>;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <LiveProvider>
      <TokenGate>{children}</TokenGate>
    </LiveProvider>
  );
}
