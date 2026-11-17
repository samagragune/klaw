"use client";

import Link from "next/link";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import { auth, getApiUrl, setApiUrl, setToken } from "@/lib/api";
import { CopyCode, LogoMark } from "@/components/site";
import { ClawMachine } from "@/components/claw";
import { INSTALL_SUPERVISOR, START_SUPERVISOR } from "@/lib/site-constants";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";

type Supervisor = "checking" | "up" | "down";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (o: { client_id: string; callback: (r: { credential: string }) => void }) => void;
          renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
        };
      };
    };
  }
}

function friendly(err: unknown): string {
  const message = err instanceof Error ? err.message : "Could not sign in";
  if (message === "Failed to fetch" || message.includes("NetworkError")) {
    return /localhost|127\.0\.0\.1|\[::1\]/.test(getApiUrl())
      ? `Couldn't reach your supervisor at ${getApiUrl()}. Start it with "klaw-supervisor start", and allow access to apps on this device if your browser asks.`
      : `Couldn't reach the supervisor at ${getApiUrl()}.`;
  }
  return message;
}

export default function LoginPage() {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supervisor, setSupervisor] = useState<Supervisor>("checking");
  const [googleOk, setGoogleOk] = useState(false);
  const [editing, setEditing] = useState(false);
  const [address, setAddress] = useState("");
  const googleButton = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Read once on the client: the address lives in this browser.
    setAddress(getApiUrl());
    if (new URLSearchParams(window.location.search).get("mode") === "signup") setMode("signup");
    auth
      .providers()
      .then((p) => {
        setSupervisor("up");
        setGoogleOk(Boolean(GOOGLE_CLIENT_ID) && p.google_client_ids.includes(GOOGLE_CLIENT_ID));
      })
      .catch(() => setSupervisor("down"));
  }, []);

  const finish = (token: string) => {
    setToken(token);
    window.location.replace("/dashboard");
  };

  const onGoogle = useCallback(async (response: { credential: string }) => {
    setBusy(true);
    setError(null);
    try {
      finish((await auth.google(response.credential)).token);
    } catch (err) {
      setError(friendly(err));
      setBusy(false);
    }
  }, []);

  const renderGoogle = useCallback(() => {
    if (!googleOk || !window.google || !googleButton.current) return;
    window.google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: onGoogle });
    window.google.accounts.id.renderButton(googleButton.current, {
      theme: "filled_black",
      size: "large",
      shape: "rectangular",
      text: mode === "signup" ? "signup_with" : "signin_with",
      width: 360,
    });
  }, [googleOk, mode, onGoogle]);

  useEffect(() => {
    renderGoogle();
  }, [renderGoogle]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session =
        mode === "signin" ? await auth.login(email, password) : await auth.signup(email, password, name || undefined);
      finish(session.token);
    } catch (err) {
      setError(friendly(err));
      setBusy(false);
    }
  };

  const status = {
    checking: { color: "var(--text-faint)", text: "checking…" },
    up: { color: "var(--success)", text: "connected" },
    down: { color: "var(--failed)", text: "not reachable" },
  }[supervisor];

  return (
    <div className="grid min-h-screen bg-bg lg:grid-cols-2">
      {googleOk ? <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onLoad={renderGoogle} /> : null}

      {/* Brand side */}
      <div className="grid-bg hidden flex-col justify-between border-r border-line p-12 lg:flex">
        <Link href="/" className="flex items-center gap-2.5 text-ink">
          <LogoMark />
          <span className="display display-bold text-[22px]">Klaw</span>
        </Link>
        <div>
          <div className="bracket mb-6">Sign in to your supervisor</div>
          <h1 className="display text-[64px]">
            <span className="display-bold block">One dashboard.</span>
            <span className="display-light block">Every agent.</span>
          </h1>
          <p className="mt-6 max-w-md text-[16px] leading-relaxed text-muted">
            Your account lives on the supervisor you run. This page talks to it directly from your
            browser — nothing passes through anyone else.
          </p>
        </div>
        <div className="flex items-end justify-between">
          <div className="label-mono text-faint">runs locally · open source</div>
          <ClawMachine width={70} />
        </div>
      </div>

      {/* Form side */}
      <div className="relative flex items-center justify-center overflow-hidden px-5 py-12">
        <div className="glow glow-accent glow-drift right-[-20%] top-[10%] size-[520px]" />
        <div className="glass-strong relative w-full max-w-[440px] p-8">
          <Link href="/" className="mb-10 flex items-center gap-2.5 text-ink lg:hidden">
            <LogoMark />
            <span className="display display-bold text-[22px]">Klaw</span>
          </Link>
          <div className="bracket mb-3">{mode === "signin" ? "Sign in" : "Create account"}</div>
          <h2 className="display display-bold text-[40px]">
            {mode === "signin" ? "Welcome back" : "Get started"}
          </h2>

          {/* Supervisor address + status */}
          <div className="glass mt-6 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <span className="label-mono text-faint">Supervisor</span>
              <span className="label-mono flex items-center gap-1.5" style={{ color: status.color }}>
                <span className="size-1.5 rounded-full bg-current" />
                {status.text}
              </span>
            </div>
            {editing ? (
              <form
                className="mt-2 flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  setApiUrl(address);
                  window.location.reload();
                }}
              >
                <input
                  autoFocus
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  className="h-9 min-w-0 flex-1 border border-line-strong bg-bg px-2 font-mono text-[12px] text-ink outline-none focus:border-[var(--accent)]"
                />
                <button type="submit" className="btn btn-sm btn-light">
                  Use
                </button>
              </form>
            ) : (
              <div className="mt-1 flex items-center justify-between gap-3">
                <span className="truncate font-mono text-[12.5px] text-ink">{address}</span>
                <button type="button" className="label-mono text-muted hover:text-ink" onClick={() => setEditing(true)}>
                  change
                </button>
              </div>
            )}
          </div>

          {supervisor === "down" ? (
            <div className="mt-4 space-y-3 border border-[var(--failed)]/40 p-4 text-sm text-muted">
              <p>
                No supervisor answered at that address. Start one on this machine (Python 3.11+), then
                reload — and allow access to apps on this device if your browser asks:
              </p>
              <CopyCode code={`${INSTALL_SUPERVISOR}\n${START_SUPERVISOR}`} />
            </div>
          ) : null}

          {googleOk ? (
            <>
              <div className="mt-6 flex min-h-[44px] justify-center" ref={googleButton} />
              <div className="label-mono my-5 flex items-center gap-3 text-faint">
                <span className="h-px flex-1 bg-[var(--border)]" /> or with email <span className="h-px flex-1 bg-[var(--border)]" />
              </div>
            </>
          ) : (
            <div className="mt-6">
              <button
                type="button"
                disabled
                title="Google sign-in is not enabled on this supervisor yet"
                className="btn w-full cursor-not-allowed text-faint"
              >
                Continue with Google
              </button>
              <p className="mt-2 text-[11px] text-faint">
                {GOOGLE_CLIENT_ID
                  ? "Your supervisor doesn't accept Google sign-in yet — add GOOGLE_CLIENT_IDS to its config.env."
                  : "Google sign-in is not configured for this site yet."}{" "}
                <Link href="/docs/install" className="underline">How</Link>
              </p>
              <div className="label-mono my-5 flex items-center gap-3 text-faint">
                <span className="h-px flex-1 bg-[var(--border)]" /> with email <span className="h-px flex-1 bg-[var(--border)]" />
              </div>
            </div>
          )}

          <form className="space-y-3" onSubmit={submit}>
            {mode === "signup" ? (
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Name (optional)"
                autoComplete="name"
                className="h-11 w-full border border-line-strong bg-bg px-3 text-sm text-ink outline-none focus:border-[var(--accent)]"
              />
            ) : null}
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
              className="h-11 w-full border border-line-strong bg-bg px-3 text-sm text-ink outline-none focus:border-[var(--accent)]"
            />
            <input
              type="password"
              required
              minLength={mode === "signup" ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "signup" ? "Password (10+ characters)" : "Password"}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              className="h-11 w-full border border-line-strong bg-bg px-3 text-sm text-ink outline-none focus:border-[var(--accent)]"
            />
            {error ? <p className="text-[13px] leading-relaxed text-[var(--failed)]">{error}</p> : null}
            <button type="submit" disabled={busy} className="btn btn-accent w-full">
              {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>

          <button
            type="button"
            className="mt-5 text-sm text-muted hover:text-ink"
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setError(null);
            }}
          >
            {mode === "signin" ? "No account on this supervisor? Create one →" : "Already have an account? Sign in →"}
          </button>
        </div>
      </div>
    </div>
  );
}
