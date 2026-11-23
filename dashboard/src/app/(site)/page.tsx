"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

import { PixelScene, type SceneName } from "@/components/pixel-scene";
import { CopyCode } from "@/components/site";

const delay = (s: number): CSSProperties => ({ animationDelay: `${s}s` });

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
    <div className="absolute right-5 top-6 z-20 hidden items-center gap-2 text-[13px] font-medium text-ink/80 md:flex">
      <span className="size-1.5 rounded-full bg-[var(--success)]" />
      <span>{now ?? "--:--"}</span>
      <span className="text-ink/45">127.0.0.1</span>
    </div>
  );
}

function Hero() {
  return (
    <section className="relative h-[100svh] min-h-[680px] overflow-hidden">
      <PixelScene name="hero" position="50% 75%" />
      {/* scrim: keeps the headline on clean, dark ground */}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(14,12,18,0.92)_0%,rgba(14,12,18,0.7)_26%,rgba(14,12,18,0)_52%)]" />
      <LocalClock />
      <div className="absolute inset-x-0 top-[104px] z-10 mx-auto max-w-[1040px] px-5 text-center">
        <h1 className="display text-[42px] leading-[1.06] text-ink md:text-[64px]">
          The supervisor your <span className="hot">AI agents</span> answer to
        </h1>
        <p className="mx-auto mt-5 max-w-[560px] text-[16.5px] leading-relaxed text-ink/75">
          Klaw runs on your machine and watches Claude Code and OpenAI Agents SDK apps work: it plans, checks,
          verifies, and steps in.
        </p>
      </div>
      <div className="glass-photo absolute bottom-4 left-4 z-10 w-[min(500px,calc(100%-2rem))] rounded-[22px] p-7 md:bottom-6 md:left-6">
        <h2 className="display text-[30px] leading-[1.12] md:text-[34px]">An agent that keeps your agents honest</h2>
        <p className="mt-4 text-[15px] leading-relaxed text-ink/75">
          It reads the real diff, runs your tests, and sends unfinished work back with exact instructions.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-5">
          <Link href="/login?mode=signup" className="btn btn-accent">
            Get started
          </Link>
          <Link href="/docs/agent" className="arrow text-[14.5px] text-ink">
            Get to know Klaw
          </Link>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------- feature visuals */

function Window({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="well flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <span className="size-2 rounded-full bg-[#3a3440]" />
        <span className="size-2 rounded-full bg-[#3a3440]" />
        <span className="size-2 rounded-full bg-[#3a3440]" />
        <span className="label-mono ml-2 text-faint">{title}</span>
        <span className="ml-auto">{right}</span>
      </div>
      <div className="relative flex-1 p-5">{children}</div>
    </div>
  );
}

function PlanVisual() {
  const reqs = ["Fix quantity in cart.total", "Tests must pass", "Never edit the tests"];
  return (
    <Window title="plan · new task" right={<span className="label-mono text-amber">planning</span>}>
      <div className="seq flex items-center gap-3 rounded-lg border border-line bg-[#17141c] px-3 py-2.5" style={delay(0)}>
        <div className="h-7 w-6"><PixelScene name="robot" fit="contain" /></div>
        <span className="text-[13.5px] text-ink/85">&ldquo;Fix cart.total for quantity. Make the tests pass.&rdquo;</span>
      </div>
      <div className="mt-4 space-y-2.5">
        {reqs.map((r, i) => (
          <div key={r} className="seq flex items-center gap-3" style={delay(0.6 + i * 0.5)}>
            <svg width="20" height="20" viewBox="0 0 20 20">
              <rect x="1" y="1" width="18" height="18" rx="5" fill="none" stroke="rgba(241,233,222,0.25)" />
              <path className="tick" style={delay(0.8 + i * 0.5)} d="M5 10.5l3.2 3.2L15 6.5" fill="none" stroke="#4fd1a5" strokeWidth="2" />
            </svg>
            <span className={`text-[14px] ${i === 2 ? "text-[var(--accent)]" : "text-ink/85"}`}>{r}</span>
            {i === 2 ? <span className="label-mono rounded bg-[rgba(255,106,77,0.12)] px-1.5 py-0.5 text-[10px] text-[var(--accent)]">your rule</span> : null}
          </div>
        ))}
      </div>
      <div className="seq absolute bottom-5 left-5 right-5 flex items-center justify-between rounded-lg border border-[rgba(245,184,74,0.3)] bg-[rgba(245,184,74,0.08)] px-3 py-2" style={delay(2.4)}>
        <span className="text-[13px] text-amber">◆ Checkpoint saved</span>
        <span className="label-mono text-amber/80">a1f3c9 · undo anytime</span>
      </div>
    </Window>
  );
}

function CheckVisual() {
  const calls: [string, string, string][] = [
    ["Read", "cart.py", "allow"],
    ["Bash", "pytest -q", "allow"],
    ["Bash", "export STRIPE_KEY=sk_live_••••••", "block"],
    ["Edit", "test_cart.py", "rule"],
  ];
  return (
    <Window title="tool calls · live" right={<span className="label-mono text-[var(--success)]">● watching</span>}>
      <div className="space-y-2">
        {calls.map(([tool, arg, verdict], i) => (
          <div
            key={arg}
            className={`seq flex items-center gap-3 rounded-lg border px-3 py-2 font-mono text-[12.5px] ${
              verdict === "allow" ? "border-line bg-[#17141c]" : verdict === "block" ? "border-[rgba(255,106,77,0.5)] bg-[rgba(255,106,77,0.1)]" : "border-[rgba(245,184,74,0.4)] bg-[rgba(245,184,74,0.08)]"
            }`}
            style={delay(i * 0.7)}
          >
            <span className="w-10 text-faint">{tool}</span>
            <span className="min-w-0 flex-1 truncate text-ink/90">{arg}</span>
            <span className={verdict === "allow" ? "text-[var(--success)]" : verdict === "block" ? "text-[var(--accent)]" : "text-amber"}>
              {verdict === "allow" ? "✓ allow" : verdict === "block" ? "✕ block" : "✕ rule"}
            </span>
          </div>
        ))}
      </div>
      <div className="stamp pointer-events-none absolute right-6 top-[46%] rounded-md border-[3px] border-[var(--accent)] px-3 py-1 font-mono text-[15px] font-bold tracking-wider text-[var(--accent)]" style={delay(0)}>
        BLOCKED · 0.3s
      </div>
    </Window>
  );
}

function VerifyVisual() {
  const diff: [string, string][] = [
    [" ", "def total(items):"],
    ["-", "    return sum(i['price'] for i in items)"],
    ["+", "    return sum(i['price'] for i in items)  # unchanged"],
    [" ", ""],
    ["-", "assert total([...]) == 6"],
    ["+", "assert total([...]) == 2"],
  ];
  return (
    <Window title="verify · “done! tests pass”" right={<span className="label-mono text-faint">git diff · pytest</span>}>
      <div className="grid h-full grid-cols-[1.4fr_1fr] gap-5">
        <div className="rounded-lg border border-line bg-[#0b0a0e] p-3 font-mono text-[11.5px] leading-[1.7]">
          {diff.map(([sign, line], i) => (
            <div
              key={i}
              className={`seq whitespace-pre ${sign === "-" ? "bg-[rgba(255,106,92,0.1)] text-[#ff9a8a]" : sign === "+" ? "bg-[rgba(79,209,165,0.1)] text-[#7fe3bf]" : "text-faint"}`}
              style={delay(i * 0.25)}
            >
              {sign} {line}
            </div>
          ))}
          <div className="seq mt-2 text-amber" style={delay(1.8)}>⚠ test file edited, cart.py not fixed</div>
        </div>
        <div className="flex flex-col justify-center gap-2.5">
          {["Pass", "Incomplete", "Needs another attempt"].map((v, i) => (
            <div key={v} className={`rounded-lg border border-line px-3 py-2 text-center text-[13px] ${i === 2 ? "verdict-on" : "text-faint"}`}>
              {v}
            </div>
          ))}
          <div className="seq mt-1 text-center text-[12px] text-ink/70" style={delay(3.4)}>↩ sent back with instructions</div>
        </div>
      </div>
    </Window>
  );
}

function ActVisual() {
  const dots: [number, number, string][] = [[22, 30, "ok"], [70, 22, "ok"], [78, 64, "bad"], [36, 72, "warn"], [52, 46, "ok"], [16, 56, "ok"]];
  return (
    <Window title="sessions · watched on its own" right={<span className="label-mono text-faint">6 live</span>}>
      <div className="grid h-full grid-cols-[1fr_1.1fr] items-center gap-5">
        <div className="relative mx-auto aspect-square w-full max-w-[210px] rounded-full border border-line">
          <div className="absolute inset-[18%] rounded-full border border-line" />
          <div className="absolute inset-[36%] rounded-full border border-line" />
          <div className="orbit absolute inset-0 rounded-full bg-[conic-gradient(from_0deg,rgba(255,106,77,0.35),rgba(255,106,77,0)_22%,transparent)]" />
          {dots.map(([x, y, kind], i) => (
            <span
              key={i}
              className={`absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${kind === "bad" ? "blip bg-[var(--accent)]" : kind === "warn" ? "bg-amber" : "bg-ink/70"}`}
              style={{ left: `${x}%`, top: `${y}%` }}
            />
          ))}
        </div>
        <div className="space-y-2.5">
          <div className="seq rounded-lg border border-[rgba(245,184,74,0.35)] bg-[rgba(245,184,74,0.07)] px-3 py-2.5 text-[13px]" style={delay(0.6)}>
            <div className="font-medium text-amber">Stuck · guidance sent</div>
            <div className="mt-0.5 text-ink/70">&ldquo;Stop reinstalling; Python 3.9 is too old.&rdquo;</div>
          </div>
          <div className="seq rounded-lg border border-[rgba(255,106,77,0.45)] bg-[rgba(255,106,77,0.09)] px-3 py-2.5 text-[13px]" style={delay(1.8)}>
            <div className="font-medium text-[var(--accent)]">Risky · session stopped</div>
            <div className="mt-0.5 text-ink/70">Edited the tests and exposed a key.</div>
          </div>
        </div>
      </div>
    </Window>
  );
}

const FEATURES: { label: string; title: string; body: string; visual: ReactNode }[] = [
  {
    label: "01 · When a task starts",
    title: "It plans",
    body: "Klaw reads the request and the project, writes down what “done” means and how to check it, and takes a checkpoint you can undo to.",
    visual: <PlanVisual />,
  },
  {
    label: "02 · Before every action",
    title: "It checks",
    body: "Allow, guide, block, ask you, or stop, with its reasoning. Your rules in plain words apply to every call; a live key never gets through.",
    visual: <CheckVisual />,
  },
  {
    label: "03 · When it says “done”",
    title: "It verifies",
    body: "It reads the real diff and runs your tests, then answers Pass, Incomplete or Needs another attempt, and sends unfinished work back.",
    visual: <VerifyVisual />,
  },
  {
    label: "04 · All the time",
    title: "It acts on its own",
    body: "It watches every live session, investigates the ones that look stuck or risky, and guides or stops them without waiting for you.",
    visual: <ActVisual />,
  },
];

/* --------------------------------------------------------- how it fits */

const STEPS: { n: string; title: string; body: string; scene: SceneName; code?: string; cta?: [string, string] }[] = [
  {
    n: "1",
    title: "Run Klaw on your machine",
    body: "One command. It keeps its own database and never sends your code anywhere.",
    scene: "terminal",
    code: 'pip install "git+https://github.com/samagragune/klaw.git#subdirectory=backend"\nklaw-supervisor start',
  },
  {
    n: "2",
    title: "Connect your agents",
    body: "Claude Code through its hooks, Agents SDK apps with one changed line. Work continues as usual.",
    scene: "robots",
    code: "klaw-companion setup --key rl_int_…",
  },
  {
    n: "3",
    title: "Watch and steer",
    body: "Open the console: plans, blocks, verdicts and reports as they happen. Approve from your phone.",
    scene: "monitors",
    cta: ["Open the console", "/login?mode=signup"],
  },
];

function HowItFits() {
  return (
    <section id="how" className="mx-auto max-w-[1180px] px-5 py-[110px] md:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="bracket">How it fits into your work</div>
          <h2 className="display mt-4 max-w-[640px] text-[38px] leading-[1.1] md:text-[50px]">
            Three steps, <span className="quiet">and your agents are supervised.</span>
          </h2>
        </div>
        <Link href="/docs/quickstart" className="arrow text-[14px] text-ink/80">
          Full quickstart
        </Link>
      </div>
      <div className="relative mt-14 grid gap-6 md:grid-cols-3">
        {/* connector */}
        <div className="pointer-events-none absolute left-[16%] right-[16%] top-[26px] hidden h-px bg-[repeating-linear-gradient(90deg,rgba(255,106,77,0.6)_0_6px,transparent_6px_12px)] md:block" />
        {STEPS.map((s) => (
          <div key={s.n} className="relative flex flex-col">
            <div className="relative z-10 mx-auto grid size-[52px] place-items-center rounded-full border border-[rgba(255,106,77,0.5)] bg-[var(--bg)] font-mono text-[18px] text-[var(--accent)]">
              {s.n}
            </div>
            <div className="glass mt-6 flex flex-1 flex-col overflow-hidden">
              <div className="h-[150px] border-b border-line">
                <PixelScene name={s.scene} />
              </div>
              <div className="flex flex-1 flex-col p-6">
                <h3 className="display text-[26px]">{s.title}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-muted">{s.body}</p>
                <div className="mt-auto pt-5">
                  {s.code ? <CopyCode code={s.code} /> : null}
                  {s.cta ? (
                    <Link href={s.cta[1]} className="btn btn-accent w-full">
                      {s.cta[0]}
                    </Link>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- page */

export default function LandingPage() {
  return (
    <>
      <Hero />

      <section className="mx-auto grid max-w-[1100px] items-center gap-10 px-5 py-[120px] md:grid-cols-[240px_1fr] md:px-8">
        <div className="mx-auto h-[200px] w-[170px]">
          <PixelScene name="robot" fit="contain" />
        </div>
        <div>
          <p className="display text-[30px] leading-[1.25] md:text-[40px]">
            Coding agents are good at <span className="quiet">refactors</span>, <span className="quiet">migrations</span> and{" "}
            <span className="quiet">bug fixes</span>. They are also <span className="hot">marking their own homework.</span>
          </p>
          <p className="display mt-6 text-[30px] md:text-[40px]">They need a supervisor.</p>
        </div>
      </section>

      <section className="mx-auto max-w-[1180px] space-y-[110px] px-5 pb-[60px] md:px-8">
        {FEATURES.map((f, i) => (
          <div key={f.title} className={`grid items-center gap-10 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.3fr)] ${i % 2 ? "md:[&>*:first-child]:order-2" : ""}`}>
            <div>
              <div className="bracket">{f.label}</div>
              <h3 className="display mt-4 text-[40px] leading-[1.05] md:text-[52px]">{f.title}</h3>
              <p className="mt-4 max-w-[420px] text-[15.5px] leading-relaxed text-muted">{f.body}</p>
            </div>
            <div className="glass h-[340px] p-2.5">{f.visual}</div>
          </div>
        ))}
      </section>

      <HowItFits />

      <section className="mx-auto max-w-[1240px] px-3 py-6 md:px-6">
        <div className="relative h-[560px] overflow-hidden rounded-[26px] border border-line">
          <PixelScene name="monitors" />
          <div className="glass-float absolute right-5 top-5 w-[min(360px,calc(100%-2.5rem))] rounded-[14px] p-4 text-[13px] leading-snug">
            <div className="flex items-center justify-between font-semibold">
              <span>Klaw &middot; claude_code</span>
              <span className="font-normal text-ink/50">now</span>
            </div>
            <p className="mt-1 text-ink/75">Blocked: the command would expose a Stripe live key.</p>
          </div>
          <div className="glass-photo absolute bottom-5 left-5 w-[min(540px,calc(100%-2.5rem))] rounded-[22px] p-8">
            <h2 className="display text-[32px] leading-[1.12] md:text-[40px]">Talk to it in plain language</h2>
            <p className="mt-4 text-[15px] leading-relaxed text-ink/75">
              Ask what an agent touched, tell it to stop one, or give it a rule it enforces from now on.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[760px] px-5 py-[120px] text-center md:px-8">
        <div className="mx-auto mb-6 h-[70px] w-[34px]">
          <PixelScene name="claw" fit="contain" />
        </div>
        <h2 className="display text-[36px] leading-[1.12] md:text-[48px]">
          Let your agents work.
          <br />
          <span className="hot">Klaw makes sure it&apos;s right.</span>
        </h2>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <Link href="/login?mode=signup" className="btn btn-accent">
            Get started
          </Link>
          <Link href="/docs" className="btn">
            Read the docs
          </Link>
        </div>
      </section>
    </>
  );
}
