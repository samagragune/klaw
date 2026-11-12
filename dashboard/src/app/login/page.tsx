"use client";

import { useState } from "react";
import { getApiUrl, setApiUrl, auth, setToken } from "@/lib/api";
import { Button, Card, Input } from "@/components/ui";
import { BackendStatus } from "@/components/backend-status";

export default function LoginPage() {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingBackend, setEditingBackend] = useState(false);
  const [backend, setBackend] = useState(() => getApiUrl());

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session =
        mode === "signin"
          ? await auth.login(email, password)
          : await auth.signup(email, password, name || undefined);
      setToken(session.token);
      window.location.replace("/dashboard");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not sign in";
      if (message === "Failed to fetch" || message.includes("NetworkError")) {
        const local = /localhost|127\.0\.0\.1|\[::1\]/.test(getApiUrl());
        setError(
          local
            ? `Couldn't reach the supervisor at ${getApiUrl()}. Make sure it is running on this computer, and allow "access other apps and services on this device" when your browser asks (or in this site's settings, next to the address bar).`
            : `Couldn't reach the supervisor at ${getApiUrl()}. Check the address below and that the supervisor is running.`,
        );
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg">
      <BackendStatus />
    <div className="grid min-h-[90vh] place-items-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <div className="grid size-7 place-items-center rounded bg-[var(--accent)] text-xs font-bold text-white">
            R
          </div>
          <div>
            <div className="text-sm font-semibold text-ink">Reliability</div>
            <div className="text-[11px] text-faint">supervisor for AI agents</div>
          </div>
        </div>
        <Card className="p-5">
          <h1 className="text-base font-semibold text-ink">
            {mode === "signin" ? "Sign in" : "Create your workspace"}
          </h1>
          <p className="mt-1 text-xs text-muted">
            {mode === "signin"
              ? "Supervise Claude Code and OpenAI Agents SDK agents from one place."
              : "Each account is its own isolated workspace."}
          </p>
          <form className="mt-4 space-y-3" onSubmit={submit}>
            {mode === "signup" ? (
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Name (optional)"
                autoComplete="name"
              />
            ) : null}
            <Input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
            />
            <Input
              type="password"
              required
              minLength={mode === "signup" ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "signup" ? "Password (10+ characters)" : "Password"}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
            />
            {error ? <p className="text-xs text-[var(--failed)]">{error}</p> : null}
            <Button type="submit" variant="primary" disabled={busy} className="w-full justify-center">
              {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
            </Button>
          </form>
          <button
            type="button"
            className="mt-4 text-xs text-muted hover:text-ink"
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setError(null);
            }}
          >
            {mode === "signin" ? "No account? Create one" : "Have an account? Sign in"}
          </button>
        </Card>
        <div className="mt-4 text-center text-[11px] text-faint">
          {editingBackend ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setApiUrl(backend);
                window.location.reload();
              }}
            >
              <Input
                autoFocus
                value={backend}
                onChange={(e) => setBackend(e.target.value)}
                placeholder="https://… or http://localhost:8000"
              />
              <Button type="submit">Use</Button>
            </form>
          ) : (
            <>
              Supervisor: <span className="font-mono">{getApiUrl()}</span>{" "}
              <button type="button" className="underline hover:text-ink" onClick={() => setEditingBackend(true)}>
                change
              </button>
            </>
          )}
        </div>
      </div>
    </div>
    </div>
  );
}
