import Link from "next/link";
import {
  Activity,
  BadgeCheck,
  Ban,
  Gauge,
  Hand,
  Repeat2,
  ShieldAlert,
  TimerOff,
} from "lucide-react";
import { CopyCode, Section, SupervisorTerminal } from "@/components/site";
import { INSTALL_COMPANION, INSTALL_SUPERVISOR, START_SUPERVISOR } from "@/lib/site-constants";

const CAPABILITIES = [
  {
    icon: Repeat2,
    title: "Loops that go nowhere",
    body: "The same call returning the same result, or failing the same way, is stopped with feedback the agent reads — so it changes course instead of burning another hour.",
  },
  {
    icon: ShieldAlert,
    title: "Risky operations",
    body: "Recursive deletes, force-pushes, hard resets, dropped tables, piping the internet into a shell, credential files, deploys — held for a human or refused, by your policy.",
  },
  {
    icon: Hand,
    title: "Human approval, remotely",
    body: "An agent asks; you answer from the dashboard on any device. It waits at the exact call, with a countdown and a safe default if nobody answers.",
  },
  {
    icon: BadgeCheck,
    title: "Answers that miss the task",
    body: "Optional output review checks finished work against the request and whether the claims are backed by tool results — and can send the agent back once with the gaps.",
  },
  {
    icon: TimerOff,
    title: "Stalls and runaway budgets",
    body: "Tasks that go quiet, run too long, call too many tools, or spend too many tokens raise an issue with the evidence attached.",
  },
  {
    icon: Gauge,
    title: "A reliability score you can explain",
    body: "Every agent gets a score from a published formula over its own history — task success, incident-free runs, tool success. No black box.",
  },
];

const MATRIX: Array<[string, string, string]> = [
  ["See every prompt, tool call, sub-agent and answer", "yes", "yes"],
  ["Model calls and token usage", "yes", "yes"],
  ["Block a tool call, with a reason the agent sees", "yes", "yes"],
  ["Hold a call for human approval", "yes", "yes"],
  ["Answer the platform's own permission prompts", "yes", "—"],
  ["Send unfinished work back with what's missing", "yes", "yes"],
  ["Stop", "yes", "yes"],
  ["Pause and resume", "no", "yes"],
];

const USE_CASES = [
  {
    tag: "Coding agents",
    title: "Claude Code on your laptop, and on your team's",
    body: "See what every session ran, approve the destructive command it wants to run while you're away from the terminal, and stop one that's stuck retrying.",
  },
  {
    tag: "Agents with real side effects",
    title: "Refunds, emails, account changes",
    body: "Put the irreversible tool behind an approval. The agent keeps working on everything else; the money only moves when a person says yes.",
  },
  {
    tag: "Headless and scheduled runs",
    title: "Agents nobody is watching",
    body: "Headless sessions deny every permission prompt. Route those prompts to the dashboard instead, and get told when a run stalls or loops.",
  },
  {
    tag: "Evaluating agents",
    title: "Before you trust one with more",
    body: "Compare agents by reliability score, read every decision the supervisor made about them, and see exactly where they went wrong.",
  },
];

const PERSONAS = [
  ["Developers using Claude Code", "One command connects a project. Your sessions, tool calls and costs show up on a dashboard you can open from your phone."],
  ["Teams shipping agent products", "Wrap an OpenAI Agents SDK app in one line and get approvals, a kill switch and an audit trail without building them."],
  ["Platform and security owners", "A single policy for what agents may do, enforced at the point of action, with a record of every decision and why."],
  ["Anyone running agents unattended", "Know when an agent is looping, stalled or about to do something you'd want to see first — and be able to stop it."],
];

const FAQ = [
  ["Where does my data go?", "To the supervisor you run — on your machine, in one SQLite file. This website is a static app in your browser that talks to that supervisor directly; there is no shared backend in between."],
  ["Does it slow my agent down?", "Observation is asynchronous. Decision points — a tool about to run — wait for one local request, typically a few milliseconds. If the supervisor is unreachable, adapters fail open and queue their events."],
  ["Do I need an API key for a model?", "No. Loop, risk, budget and stall detection, approvals and stopping are all deterministic. A Gemini key only adds optional output review and the 'ask the supervisor' chat."],
  ["Can it pause Claude Code?", "No — Claude Code's hooks can block, hold for approval, send feedback or stop, but nothing can suspend a turn mid-way. The dashboard says so instead of pretending."],
  ["Which agents are supported?", "Claude Code (through its lifecycle hooks), applications built on the OpenAI Agents SDK, and any Python agent through the SDK. Others can speak the open event protocol."],
  ["Is it free?", "Yes. The supervisor and adapters are open source and run on your own hardware; the dashboard is free to use."],
];

export default function LandingPage() {
  return (
    <>
      {/* HERO */}
      <section className="grid-bg border-b border-line">
        <div className="mx-auto grid max-w-[1280px] items-center gap-14 px-5 py-20 md:px-8 lg:grid-cols-[1.05fr_1fr] lg:py-28">
          <div>
            <div className="bracket mb-7">The supervisor for AI agents</div>
            <h1 className="display text-[56px] sm:text-[76px] lg:text-[88px]">
              <span className="display-bold block">Your agents work.</span>
              <span className="display-light block">You stay in charge.</span>
            </h1>
            <p className="mt-7 max-w-xl text-[18px] leading-relaxed text-muted">
              Run a supervisor on your own machine, connect Claude Code and OpenAI Agents SDK
              agents in one command, and watch, approve and stop their work from one dashboard.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link href="/login?mode=signup" className="btn btn-accent">
                Get started
              </Link>
              <Link href="/docs/quickstart" className="btn btn-ghost">
                Read the quickstart
              </Link>
            </div>
            <div className="label-mono mt-8 flex flex-wrap gap-x-6 gap-y-2 text-faint">
              <span>runs locally</span>
              <span>open source</span>
              <span>no model key required</span>
            </div>
          </div>
          <SupervisorTerminal />
        </div>
      </section>

      {/* STRIP */}
      <section className="border-b border-line">
        <div className="mx-auto grid max-w-[1280px] grid-cols-2 md:grid-cols-4">
          {[
            ["Claude Code", "lifecycle hooks"],
            ["OpenAI Agents SDK", "run hooks + guardrails"],
            ["Python agents", "SDK checkpoints"],
            ["Your platform", "open event protocol"],
          ].map(([name, how]) => (
            <div key={name} className="border-r border-line px-5 py-7 last:border-r-0 md:px-8">
              <div className="display display-bold text-[22px]">{name}</div>
              <div className="label-mono mt-1 text-faint">{how}</div>
            </div>
          ))}
        </div>
      </section>

      {/* HOW IT WORKS */}
      <Section
        id="how"
        label="How it works"
        light="Three steps."
        bold="Nothing to host."
        intro="The supervisor is a small program you run yourself. Agents report to it; this website is the window onto it. Your data never passes through anyone else."
      >
        <div className="grid gap-px border border-line bg-[var(--border)] lg:grid-cols-3">
          {[
            {
              n: "01",
              title: "Run the supervisor",
              body: "Python 3.11+. It creates its own database and secret on first start.",
              code: `${INSTALL_SUPERVISOR}\n${START_SUPERVISOR}`,
            },
            {
              n: "02",
              title: "Connect your agents",
              body: "Create a key under Integrations; the dashboard shows the one command to run in your project.",
              code: `${INSTALL_COMPANION}\nreliability-companion setup --api-url http://127.0.0.1:8000 --key rl_int_…`,
            },
            {
              n: "03",
              title: "Watch and decide",
              body: "Every session appears live. Set a policy, answer approvals, stop what needs stopping.",
              code: "# open the dashboard\n# Policies → control mode\n# Approvals → approve / deny",
            },
          ].map((step) => (
            <div key={step.n} className="flex flex-col gap-5 bg-bg p-7">
              <div className="flex items-baseline gap-3">
                <span className="label-mono text-[var(--accent)]">{step.n}</span>
                <h3 className="display display-bold text-[28px]">{step.title}</h3>
              </div>
              <p className="text-sm leading-relaxed text-muted">{step.body}</p>
              <div className="mt-auto">
                <CopyCode code={step.code} />
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* CAPABILITIES */}
      <Section
        id="capabilities"
        label="Capabilities"
        light="What it catches."
        bold="What it does about it."
        intro="Deterministic rules decide; a model only advises, and only if you turn it on. Every decision is recorded with the rule, the reason and what the platform could actually carry out."
      >
        <div className="grid gap-px border border-line bg-[var(--border)] sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <div key={title} className="bg-bg p-7">
              <Icon className="size-5 text-[var(--accent)]" strokeWidth={1.5} />
              <h3 className="display display-bold mt-5 text-[24px]">{title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted">{body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* CONTROL MATRIX */}
      <Section
        id="control"
        label="Control, not just traces"
        light="Watching an agent"
        bold="is not stopping it."
        intro="Reliability plugs into each platform's own extension points, so it can act before a tool runs — and it tells you plainly what each platform cannot do."
      >
        <div className="overflow-x-auto border border-line">
          <table className="w-full min-w-[620px] text-sm">
            <thead>
              <tr className="border-b border-line bg-panel">
                <th className="label-mono px-5 py-4 text-left text-faint">Capability</th>
                <th className="label-mono px-5 py-4 text-left text-faint">Claude Code</th>
                <th className="label-mono px-5 py-4 text-left text-faint">OpenAI Agents SDK</th>
              </tr>
            </thead>
            <tbody>
              {MATRIX.map(([cap, cc, oa]) => (
                <tr key={cap} className="border-b border-line last:border-0">
                  <td className="px-5 py-3.5 text-ink">{cap}</td>
                  {[cc, oa].map((v, i) => (
                    <td
                      key={i}
                      className="label-mono px-5 py-3.5"
                      style={{ color: v === "yes" ? "var(--success)" : v === "no" ? "var(--failed)" : "var(--text-faint)" }}
                    >
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {[
            ["Observe", "Record and report. Nothing about the agent changes."],
            ["Recommend", "The agent is told what's wrong. Nothing is refused."],
            ["Control", "Block, hold for approval, stop, pause — within your policy."],
          ].map(([mode, body]) => (
            <div key={mode} className="border border-line p-5">
              <div className="label-mono text-[var(--accent)]">{mode} mode</div>
              <p className="mt-2 text-sm text-muted">{body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* USE CASES */}
      <Section
        id="use-cases"
        label="Use cases"
        light="Built for agents that"
        bold="touch real things."
      >
        <div className="grid gap-px border border-line bg-[var(--border)] md:grid-cols-2">
          {USE_CASES.map((u) => (
            <div key={u.title} className="bg-bg p-8">
              <div className="label-mono text-faint">{u.tag}</div>
              <h3 className="display display-bold mt-4 text-[30px]">{u.title}</h3>
              <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted">{u.body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* WHO */}
      <Section id="who" label="Who it's for" light="If you run agents," bold="it's for you.">
        <div className="divide-y divide-[var(--border)] border-y border-line">
          {PERSONAS.map(([who, why]) => (
            <div key={who} className="grid gap-3 py-6 md:grid-cols-[1fr_1.6fr] md:gap-10">
              <div className="display display-bold text-[26px]">{who}</div>
              <p className="text-[15px] leading-relaxed text-muted">{why}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* LOCAL FIRST */}
      <Section
        id="local"
        label="Local-first by design"
        light="Your agents. Your machine."
        bold="Your record."
        className="dots-bg"
      >
        <div className="grid gap-px border border-line bg-[var(--border)] md:grid-cols-4">
          {[
            [Activity, "Runs where you run it", "One process and one SQLite file. Back it up by copying it."],
            [ShieldAlert, "Sends what's needed", "Secret-looking keys are redacted at the source; tool outputs are fingerprinted, not sent."],
            [Ban, "Fails open", "If the supervisor is down, agents keep working and queue their events."],
            [BadgeCheck, "Scoped keys", "An agent's key can report and ask — never read the dashboard or change policy."],
          ].map(([Icon, title, body]) => {
            const I = Icon as typeof Activity;
            return (
              <div key={title as string} className="bg-bg p-7">
                <I className="size-5 text-[var(--accent)]" strokeWidth={1.5} />
                <h3 className="display display-bold mt-5 text-[22px]">{title as string}</h3>
                <p className="mt-2 text-sm text-muted">{body as string}</p>
              </div>
            );
          })}
        </div>
      </Section>

      {/* FAQ */}
      <Section id="faq" label="FAQ" light="Questions," bold="answered plainly.">
        <div className="grid gap-px border border-line bg-[var(--border)] md:grid-cols-2">
          {FAQ.map(([q, a]) => (
            <div key={q} className="bg-bg p-7">
              <h3 className="text-[16px] font-semibold text-ink">{q}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{a}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* CTA */}
      <section className="grid-bg border-b border-line">
        <div className="mx-auto flex max-w-[1280px] flex-col items-start gap-8 px-5 py-24 md:px-8 lg:flex-row lg:items-end lg:justify-between">
          <h2 className="display text-[52px] md:text-[80px]">
            <span className="display-light block">Start supervising</span>
            <span className="display-bold block">in five minutes.</span>
          </h2>
          <div className="flex flex-wrap gap-3">
            <Link href="/login?mode=signup" className="btn btn-accent">
              Get started
            </Link>
            <Link href="/docs" className="btn btn-ghost">
              Documentation
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
