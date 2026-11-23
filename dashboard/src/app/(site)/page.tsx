"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { PixelScene, type SceneName } from "@/components/pixel-scene";

/* ------------------------------------------------------------------ hero */

function LocalClock() {
  const [now, setNow] = useState<string | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="absolute right-5 top-6 z-10 hidden items-center gap-2 text-[13px] font-medium text-ink/80 md:flex">
      <span className="size-1.5 rounded-full bg-[var(--success)]" />
      <span>{now ?? "--:--"}</span>
      <span className="text-ink/50">127.0.0.1</span>
    </div>
  );
}

function Hero() {
  return (
    <section className="relative h-[100svh] min-h-[640px] overflow-hidden">
      <PixelScene name="hero" position="50% 70%" />
      <LocalClock />
      <h1 className="display absolute inset-x-0 top-[104px] z-10 mx-auto max-w-[620px] px-5 text-center text-[30px] leading-[1.25] text-ink md:text-[34px]">
        Klaw, the supervisor
        <br />
        for your AI agents
      </h1>
      <div className="glass-photo absolute bottom-4 left-4 z-10 w-[min(520px,calc(100%-2rem))] rounded-[22px] p-7 md:bottom-6 md:left-6 md:p-8">
        <h2 className="display text-[34px] leading-[1.1] md:text-[40px]">An agent that keeps your agents honest</h2>
        <p className="mt-5 text-[15.5px] leading-relaxed text-white/90">
          Klaw runs on your machine beside Claude Code and OpenAI Agents SDK apps. It plans each task, checks every
          action, and verifies every &ldquo;done&rdquo; against the real diff and your tests.
        </p>
        <Link href="/docs/agent" className="arrow mt-6 text-white">
          Get to know Klaw
        </Link>
      </div>
      <p className="absolute bottom-7 right-7 z-10 hidden max-w-[260px] text-right text-[13.5px] font-medium leading-snug text-white drop-shadow md:block">
        Agents do the work.
        <br />
        Klaw makes sure it&apos;s right.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------- diagrams */

const stroke = "rgba(28,27,25,0.55)";
const faint = "rgba(28,27,25,0.22)";

function PlanDiagram() {
  return (
    <svg viewBox="0 0 320 200" className="h-full w-full">
      {[0, 1, 2, 3].map((i) => (
        <g key={i} className="float-y" style={{ animationDelay: `${i * 0.4}s` }}>
          <rect x="70" y={36 + i * 34} width="18" height="18" rx="3" fill="none" stroke={stroke} />
          {i < 2 ? <path d={`M74 ${45 + i * 34} l4 4 l7 -8`} fill="none" stroke="#ef4f3c" strokeWidth="1.6" /> : null}
          <rect x="100" y={42 + i * 34} width={[130, 96, 150, 80][i]} height="6" rx="3" fill={faint} />
        </g>
      ))}
      <rect x="252" y="150" width="16" height="16" transform="rotate(45 260 158)" fill="none" stroke={stroke} />
      <line x1="40" y1="182" x2="290" y2="182" stroke={faint} strokeDasharray="3 4" />
    </svg>
  );
}

function CheckDiagram() {
  return (
    <svg viewBox="0 0 320 200" className="h-full w-full">
      <line x1="160" y1="30" x2="160" y2="170" stroke={stroke} />
      <line x1="168" y1="30" x2="168" y2="170" stroke={stroke} />
      {[40, 80, 200, 240].map((x, i) => (
        <rect key={x} x={x} y="90" width="22" height="22" rx="3" fill="none" stroke={stroke} className="float-y" style={{ animationDelay: `${i * 0.3}s` }} />
      ))}
      <g className="float-y" style={{ animationDelay: "0.6s" }}>
        <rect x="124" y="90" width="22" height="22" rx="3" fill="none" stroke="#ef4f3c" strokeWidth="1.4" />
        <path d="M130 96 l10 10 M140 96 l-10 10" stroke="#ef4f3c" strokeWidth="1.4" />
      </g>
      <line x1="20" y1="124" x2="300" y2="124" stroke={faint} strokeDasharray="3 4" />
    </svg>
  );
}

function VerifyDiagram() {
  return (
    <svg viewBox="0 0 320 200" className="h-full w-full">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <rect key={i} x="40" y={40 + i * 20} width={[120, 90, 140, 70, 110, 96][i]} height="6" rx="3"
          fill={i === 2 ? "rgba(217,59,59,0.45)" : i === 3 ? "rgba(31,157,87,0.5)" : faint} />
      ))}
      <path d="M220 140 A 46 46 0 0 1 300 140" fill="none" stroke={faint} strokeWidth="8" />
      <path d="M262 99 A 46 46 0 0 1 300 140" fill="none" stroke="#ef4f3c" strokeWidth="8" />
      <line x1="260" y1="140" x2="286" y2="112" stroke={stroke} strokeWidth="2" className="float-y" />
      <circle cx="260" cy="140" r="4" fill={stroke} />
    </svg>
  );
}

function ActDiagram() {
  const dots = [[-60, -30], [40, -50], [70, 20], [-30, 50], [10, -10], [-80, 30]];
  return (
    <svg viewBox="0 0 320 200" className="h-full w-full">
      <g transform="translate(160 100)">
        {[30, 60, 90].map((r) => (
          <circle key={r} r={r} fill="none" stroke={faint} />
        ))}
        <g className="orbit" style={{ transformOrigin: "0 0" }}>
          <line x1="0" y1="0" x2="90" y2="0" stroke={stroke} />
        </g>
        {dots.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r="4" fill={i === 2 ? "#ef4f3c" : "none"} stroke={i === 2 ? "#ef4f3c" : stroke} />
        ))}
        <rect x="-6" y="-6" width="12" height="12" rx="2" fill="none" stroke={stroke} />
      </g>
    </svg>
  );
}

const FEATURES: { label: string; body: ReactNode; diagram: ReactNode }[] = [
  {
    label: "Plans every task",
    body: (
      <>
        <p>When a task starts, Klaw reads the request and the project and writes down what &ldquo;done&rdquo; means, how to verify it, and the risks.</p>
        <p className="mt-3">Then it takes a git checkpoint you can undo to.</p>
      </>
    ),
    diagram: <PlanDiagram />,
  },
  {
    label: "Checks every action",
    body: (
      <>
        <p>Before each tool call it allows, guides, blocks, asks you, or stops the session, with its reasoning.</p>
        <p className="mt-3">Your rules, written in plain words, apply to every call. A live key in a command never gets through.</p>
      </>
    ),
    diagram: <CheckDiagram />,
  },
  {
    label: "Verifies every “done”",
    body: (
      <>
        <p>It reads the real diff and runs your tests, then answers Pass, Incomplete, or Needs another attempt.</p>
        <p className="mt-3">Anything but Pass goes back to the agent with exact instructions.</p>
      </>
    ),
    diagram: <VerifyDiagram />,
  },
  {
    label: "Acts on its own",
    body: (
      <>
        <p>It watches live sessions, investigates the ones that look stuck, drifting or risky, and guides or stops them.</p>
        <p className="mt-3">You hear about it when a human is actually needed.</p>
      </>
    ),
    diagram: <ActDiagram />,
  },
];

/* ------------------------------------------------------------------- page */

const DOCS: { scene: SceneName; tag: string; title: string; href: string }[] = [
  { scene: "terminal", tag: "$ klaw-supervisor start", title: "Start a supervisor in one command", href: "/docs/quickstart" },
  { scene: "robots", tag: "plan · check · verify · act", title: "How the Klaw agent works", href: "/docs/agent" },
  { scene: "brain", tag: "gemini · claude · ollama", title: "Bring any model as the brain", href: "/docs/brain" },
];

export default function LandingPage() {
  return (
    <>
      <Hero />

      {/* the problem, quietly */}
      <section className="mx-auto grid max-w-[1100px] items-center gap-10 px-5 py-[120px] md:grid-cols-[280px_1fr] md:px-8">
        <div className="mx-auto h-[220px] w-[190px]">
          <PixelScene name="robot" fit="contain" />
        </div>
        <p className="display text-[30px] leading-[1.25] md:text-[38px]">
          Putting an AI agent to work on your code is as easy as typing a prompt. Knowing what it actually did{" "}
          <span className="quiet">is not.</span>
        </p>
      </section>

      <section id="how" className="mx-auto max-w-[1100px] px-5 pb-[60px] md:px-8">
        <p className="display max-w-[760px] text-[32px] leading-[1.22] md:text-[42px]">
          Coding agents have shown real skill at <span className="quiet">refactors</span>,{" "}
          <span className="quiet">migrations</span> and <span className="quiet">bug fixes</span>, but they mark their own
          homework.
        </p>
        <p className="display mt-8 text-[32px] md:text-[42px]">They need a supervisor.</p>
      </section>

      {/* four things it does */}
      <section className="mx-auto max-w-[1100px] space-y-[96px] px-5 py-[96px] md:px-8">
        {FEATURES.map((f) => (
          <div key={f.label} className="grid items-center gap-10 md:grid-cols-[300px_1fr]">
            <div>
              <div className="bracket">{f.label}</div>
              <div className="mt-4 text-[14.5px] leading-relaxed text-muted">{f.body}</div>
            </div>
            <div className="glass p-3">
              <div className="well h-[300px] p-6">{f.diagram}</div>
            </div>
          </div>
        ))}
      </section>

      {/* the product, in its room */}
      <section className="mx-auto max-w-[1240px] px-3 py-10 md:px-6">
        <div className="relative h-[620px] overflow-hidden rounded-[26px]">
          <PixelScene name="monitors" />
          <div className="glass-float absolute right-5 top-5 w-[min(360px,calc(100%-2.5rem))] rounded-[14px] p-4 text-[13px] leading-snug">
            <div className="flex items-center justify-between font-semibold">
              <span>Klaw &middot; claude_code</span>
              <span className="font-normal text-ink/50">now</span>
            </div>
            <p className="mt-1 text-ink/80">Blocked: the command would expose a Stripe live key. Decided in 0.3 s.</p>
          </div>
          <div className="glass-photo absolute bottom-5 left-5 w-[min(560px,calc(100%-2.5rem))] rounded-[22px] p-8">
            <h2 className="display text-[34px] leading-[1.12] md:text-[42px]">Supervise your agents in plain language</h2>
            <p className="mt-4 text-[15px] leading-relaxed text-white/90">
              Ask it what an agent touched, tell it to stop one, or give it a rule it will enforce from now on.
            </p>
            <Link href="/login?mode=signup" className="btn mt-6 border-white/40 bg-white/90 text-ink hover:bg-white">
              Open the console
            </Link>
          </div>
        </div>
      </section>

      {/* from the docs */}
      <section className="mx-auto max-w-[1100px] px-5 py-[96px] md:px-8">
        <h2 className="display text-[34px] md:text-[44px]">How it fits into your work</h2>
        <Link href="/docs" className="arrow mt-3 text-[14px] text-muted">
          Read the docs
        </Link>
        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {DOCS.map((d) => (
            <Link key={d.title} href={d.href} className="group block text-center">
              <div className="glass glass-hover relative h-[210px] overflow-hidden p-0">
                <PixelScene name={d.scene} />
                <span className="label-mono absolute inset-x-0 top-5 text-center text-[11.5px] text-ink/70">{d.tag}</span>
              </div>
              <p className="mt-4 text-[15px] font-medium">{d.title}</p>
              <p className="mt-1 text-[13px] text-muted group-hover:text-ink">Read in the docs</p>
            </Link>
          ))}
        </div>
      </section>

      {/* closing */}
      <section className="mx-auto max-w-[760px] px-5 py-[110px] text-center md:px-8">
        <div className="mx-auto mb-6 h-[70px] w-[34px]">
          <PixelScene name="claw" fit="contain" />
        </div>
        <h2 className="display text-[34px] leading-[1.15] md:text-[44px]">
          We&apos;re building the supervisor
          <br />
          your agents answer to
        </h2>
        <p className="mt-5 text-[14px] text-muted">
          If that sounds useful,{" "}
          <Link href="/docs/quickstart" className="arrow text-ink">
            start it on your machine
          </Link>
        </p>
      </section>
    </>
  );
}
