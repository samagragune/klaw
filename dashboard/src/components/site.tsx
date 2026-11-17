"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { Check, Copy, GitBranch } from "lucide-react";

import { REPO_URL } from "@/lib/site-constants";
import { ClawMark } from "./claw";

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
  { href: "/#capabilities", label: "Capabilities" },
  { href: "/#use-cases", label: "Use cases" },
  { href: "/docs", label: "Docs" },
];

export function SiteNav() {
  return (
    <header className="glass-strong sticky top-0 z-40 border-x-0 border-t-0">
      <div className="mx-auto flex h-16 max-w-[1280px] items-center gap-8 px-5 md:px-8">
        <Wordmark />
        <nav className="hidden items-center gap-7 md:flex">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href} className="label-mono text-[12px] text-muted hover:text-ink">
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <a href={REPO_URL} className="hidden items-center gap-1.5 px-2 text-muted hover:text-ink sm:flex" aria-label="Source on GitHub">
            <GitBranch className="size-4" />
            <span className="label-mono text-[12px]">Source</span>
          </a>
          <Link href="/login" className="btn btn-sm btn-ghost hidden sm:inline-flex">
            Sign in
          </Link>
          <Link href="/login?mode=signup" className="btn btn-sm btn-accent">
            Get started
          </Link>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  const cols = [
    {
      title: "Product",
      links: [
        ["How it works", "/#how"],
        ["Capabilities", "/#capabilities"],
        ["Use cases", "/#use-cases"],
        ["Who it's for", "/#who"],
      ],
    },
    {
      title: "Docs",
      links: [
        ["Quickstart", "/docs/quickstart"],
        ["Connect Claude Code", "/docs/claude-code"],
        ["Connect OpenAI Agents SDK", "/docs/openai-agents"],
        ["Limitations", "/docs/limitations"],
      ],
    },
    {
      title: "Project",
      links: [
        ["Source code", REPO_URL],
        ["Event protocol", "/docs/protocol"],
        ["Security & privacy", "/docs/security"],
        ["Sign in", "/login"],
      ],
    },
  ];
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-[1280px] gap-10 px-5 py-14 md:grid-cols-[1.4fr_1fr_1fr_1fr] md:px-8">
        <div className="space-y-4">
          <Wordmark />
          <p className="max-w-xs text-sm text-muted">
            The supervisor for AI agents. Runs on your machine; this site is only the window onto it.
          </p>
        </div>
        {cols.map((col) => (
          <div key={col.title}>
            <div className="label-mono mb-4 text-faint">{col.title}</div>
            <ul className="space-y-2.5">
              {col.links.map(([label, href]) => (
                <li key={href}>
                  <Link href={href} className="text-sm text-muted hover:text-ink">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-line">
        <div className="label-mono mx-auto flex max-w-[1280px] flex-wrap justify-between gap-3 px-5 py-5 text-faint md:px-8">
          <span>Klaw · open source</span>
          <span>Your data stays on your supervisor</span>
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
      <div className="glow glow-accent glow-drift -right-40 top-10 size-[520px]" />
      <div className="glow glow-accent left-[22%] top-[52%] size-[480px] opacity-60" />
      <div className="glow glow-white -left-40 bottom-0 size-[420px]" />
      <div className="relative mx-auto max-w-[1280px] px-5 py-20 md:px-8 md:py-28">
        <div className="mb-6 flex items-center gap-3">
          <ClawMark size={18} className="claw-bob text-ink" />
          <span className="bracket">{label}</span>
        </div>
        <h2 className="display text-[44px] md:text-[64px]">
          <span className="display-light block">{light}</span>
          <span className="display-bold block">{bold}</span>
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
    <div className="ticks glass-strong">
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
