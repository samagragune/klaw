"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { Check, Copy, GitBranch } from "lucide-react";

import { REPO_URL } from "@/lib/site-constants";
import { ClawMark } from "./claw";
import { PixelScene } from "./pixel-scene";

/* ----------------------------------------------------------------- brand */

export function LogoMark({ size = 22 }: { size?: number }) {
  return <ClawMark size={size + 4} />;
}

export function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2.5 text-ink">
      <LogoMark />
      <span className="display display-bold text-[22px] tracking-wide">Klaw</span>
    </Link>
  );
}

/* --------------------------------------------------------------- chrome */

const NAV = [
  { href: "/#how", label: "How it works" },
  { href: "/docs", label: "Docs" },
  { href: REPO_URL, label: "Source" },
];

/** A floating glass pill, centred over whatever is beneath it. */
export function SiteNav() {
  return (
    <header className="pointer-events-none fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <nav className="glass-float pointer-events-auto flex items-center gap-1 rounded-[14px] p-1.5">
        <Link href="/" aria-label="Klaw home" className="press grid size-9 place-items-center rounded-[10px] bg-white/70 text-ink">
          <ClawMark size={20} />
        </Link>
        {NAV.map((item) => (
          <Link key={item.href} href={item.href} className="press hidden rounded-[10px] px-3 py-2 text-[14.5px] font-medium text-ink/80 hover:bg-white/60 hover:text-ink sm:block">
            {item.label}
          </Link>
        ))}
        <Link href="/login" className="press rounded-[10px] px-3 py-2 text-[14.5px] font-medium text-ink/80 hover:bg-white/60 hover:text-ink">
          Sign in
        </Link>
        <Link href="/login?mode=signup" className="btn btn-sm btn-accent ml-1">
          Get started
        </Link>
      </nav>
    </header>
  );
}

export function SiteFooter() {
  const links: [string, string][] = [
    ["Home", "/"],
    ["Docs", "/docs"],
    ["Quickstart", "/docs/quickstart"],
    ["The Klaw agent", "/docs/agent"],
    ["Security", "/docs/security"],
    ["Source", REPO_URL],
    ["Sign in", "/login"],
  ];
  return (
    <footer className="mt-10">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-4 px-5 py-8 md:px-8">
        <nav className="flex flex-wrap gap-x-5 gap-y-2">
          {links.map(([label, href]) => (
            <Link key={label} href={href} className="text-[13.5px] text-muted hover:text-ink">
              {label}
            </Link>
          ))}
        </nav>
        <a href={REPO_URL} className="btn btn-sm" aria-label="Klaw on GitHub">
          <GitBranch className="size-4" /> Star on GitHub
        </a>
      </div>
      <div className="relative h-[200px] overflow-hidden">
        <PixelScene name="line" />
        <div className="absolute inset-x-0 bottom-0 flex justify-between px-5 pb-4 text-[12.5px] text-white/90 md:px-8">
          <span>&copy; Klaw &middot; open source, MIT</span>
          <span>Runs on your machine</span>
        </div>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------- building blocks */

export function Section({
  id,
  label,
  light,
  bold,
  intro,
  children,
  className = "",
}: {
  id?: string;
  label: string;
  light: string;
  bold: string;
  intro?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`relative overflow-hidden border-b border-line ${className}`}>
      <div className="relative mx-auto max-w-[1280px] px-5 py-20 md:px-8 md:py-28">
        <div className="mb-6">
          <span className="bracket">{label}</span>
        </div>
        <h2 className="display text-[36px] sm:text-[44px] md:text-[56px]">
          <span className="display-light">{light}</span> <span className="text-faint">{bold}</span>
        </h2>
        {intro ? <p className="mt-6 max-w-2xl text-[17px] leading-relaxed text-muted">{intro}</p> : null}
        {children ? <div className="mt-14">{children}</div> : null}
      </div>
    </section>
  );
}

export function CopyCode({ code, prompt = "$" }: { code: string; prompt?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="glass group flex items-start gap-3 px-4 py-3">
      <pre className="terminal min-w-0 flex-1 overflow-x-auto whitespace-pre text-ink">
        {code.split("\n").map((line, i) => (
          <div key={i}>
            {prompt && !line.startsWith("#") ? <span className="text-[var(--accent)]">{prompt} </span> : null}
            <span className={line.startsWith("#") ? "text-faint" : ""}>{line}</span>
          </div>
        ))}
      </pre>
      <button
        type="button"
        aria-label="Copy"
        className="mt-0.5 shrink-0 text-faint hover:text-ink"
        onClick={() =>
          navigator.clipboard.writeText(code.split("\n").filter((l) => !l.startsWith("#")).join("\n")).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          })
        }
      >
        {copied ? <Check className="size-4 text-[var(--success)]" /> : <Copy className="size-4" />}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------- hero terminal */

type Line = { t: string; tone?: "ok" | "warn" | "bad" | "dim" | "accent" };

/** Decisions taken from live runs against real Claude Code and Agents SDK agents. */
const FEED: Line[] = [
  { t: "$ klaw-supervisor start", tone: "accent" },
  { t: "listening on 127.0.0.1:8000 · sqlite · 2 integrations", tone: "dim" },
  { t: "claude_code   PROMPT    Delete build_artifacts recursively" },
  { t: "claude_code   TOOL      PowerShell Remove-Item -Recurse -Force" },
  { t: "supervisor    APPROVAL  rule fs.recursive_delete → waiting on operator", tone: "warn" },
  { t: "operator      APPROVED  via dashboard · 1.5s", tone: "ok" },
  { t: "openai_agents HANDOFF   Triage agent → Billing agent" },
  { t: "openai_agents TOOL      issue_refund {invoice: INV-3002, amount: 20}" },
  { t: "supervisor    APPROVAL  policy.approval_required → waiting", tone: "warn" },
  { t: "claude_code   TOOL      Bash curl -s -m 2 http://127.0.0.1:9/health  ×4" },
  { t: "supervisor    BLOCK     loop.repeated_failure — same call failed 3×", tone: "bad" },
  { t: "claude_code   AGENT     \"a supervisor hook blocked a repeated failed call\"", tone: "dim" },
];

export function SupervisorTerminal() {
  const [shown, setShown] = useState(1);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setShown(FEED.length);
      return;
    }
    const id = setInterval(() => setShown((n) => (n >= FEED.length ? 1 : n + 1)), 900);
    return () => clearInterval(id);
  }, []);
  const tone = (t?: Line["tone"]) =>
    ({
      ok: "text-[var(--success)]",
      warn: "text-[var(--warning)]",
      bad: "text-[var(--failed)]",
      dim: "text-faint",
      accent: "text-[var(--accent)]",
    })[t ?? "dim"] ?? "";
  return (
    <div className="code-glass overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="flex gap-1.5">
          <span className="size-2 rounded-full bg-line-strong" />
          <span className="size-2 rounded-full bg-line-strong" />
          <span className="size-2 rounded-full bg-line-strong" />
        </div>
        <span className="label-mono text-faint">supervisor · live feed</span>
        <span className="label-mono flex items-center gap-1.5 text-[var(--success)]">
          <span className="pulse size-1.5 rounded-full bg-current" /> live
        </span>
      </div>
      <div className="terminal h-[330px] overflow-hidden px-4 py-3">
        {FEED.slice(0, shown).map((line, i) => (
          <div key={i} className={`row-in truncate ${line.tone ? tone(line.tone) : "text-ink"}`}>
            {line.t}
          </div>
        ))}
        <div className="caret" />
      </div>
      <div className="grid grid-cols-3 border-t border-line">
        {[
          ["decisions", "50"],
          ["approvals", "4"],
          ["blocked", "1"],
        ].map(([k, v]) => (
          <div key={k} className="border-r border-line px-4 py-3 last:border-r-0">
            <div className="label-mono text-faint">{k}</div>
            <div className="display display-bold text-2xl">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
