import Link from "next/link";
import type { ReactNode } from "react";
import { CopyCode } from "./site";
import { INSTALL_COMPANION, INSTALL_SDK, INSTALL_SDK_ONLY, INSTALL_SUPERVISOR, START_SUPERVISOR } from "@/lib/site-constants";

/* Small typographic helpers so pages read like documentation. */
const H = ({ children }: { children: ReactNode }) => (
  <h2 className="display display-bold mt-12 mb-4 text-[30px]">{children}</h2>
);
const P = ({ children }: { children: ReactNode }) => (
  <p className="my-4 text-[15px] leading-[1.75] text-muted">{children}</p>
);
const C = ({ children }: { children: ReactNode }) => (
  <code className="border border-line bg-panel px-1.5 py-0.5 font-mono text-[12.5px] text-ink">{children}</code>
);
const UL = ({ items }: { items: ReactNode[] }) => (
  <ul className="my-4 space-y-2.5">
    {items.map((item, i) => (
      <li key={i} className="flex gap-3 text-[15px] leading-relaxed text-muted">
        <span className="mt-2.5 size-1.5 shrink-0 bg-[var(--accent)]" />
        <span>{item}</span>
      </li>
    ))}
  </ul>
);
const Note = ({ children }: { children: ReactNode }) => (
  <div className="glass my-6 border-l-2 border-l-[var(--accent)] px-5 py-4 text-sm leading-relaxed text-muted">{children}</div>
);
const Table = ({ head, rows }: { head: string[]; rows: ReactNode[][] }) => (
  <div className="glass my-6 overflow-x-auto">
    <table className="w-full min-w-[520px] text-sm">
      <thead>
        <tr className="border-b border-line bg-panel">
          {head.map((h) => (
            <th key={h} className="label-mono px-4 py-3 text-left text-faint">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-line last:border-0">
            {r.map((c, j) => (
              <td key={j} className="px-4 py-3 align-top text-muted">
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);
const Code = ({ code }: { code: string }) => (
  <div className="my-5">
    <CopyCode code={code} />
  </div>
);
const Snippet = ({ code }: { code: string }) => (
  <pre className="terminal glass my-5 overflow-x-auto px-4 py-3 text-ink">{code}</pre>
);

export type DocPage = { slug: string; group: string; title: string; summary: string; body: ReactNode };

export const DOCS: DocPage[] = [
  {
    slug: "",
    group: "Start here",
    title: "Introduction",
    summary: "What Klaw is, how the pieces fit, and who it is for.",
    body: (
      <>
        <P>
          Klaw is a supervisor for AI agents. Your agents keep running where they already run —
          Claude Code in your terminal, an OpenAI Agents SDK app on your server — and report what they
          do to a supervisor you run yourself. The supervisor watches for loops, risky operations,
          stalls and work that misses its objective, and acts on them when your policy allows.
        </P>
        <H>Three parts</H>
        <Table
          head={["Part", "What it is", "Where it runs"]}
          rows={[
            ["Supervisor", "A small Python server with one SQLite file. All rules, decisions and history live here.", "Your machine"],
            ["Adapters", "Thin connectors: Claude Code hooks, an Agents SDK wrapper, a Python SDK.", "Next to each agent"],
            ["Dashboard", "This website. A static app in your browser that talks to your supervisor directly.", "Your browser"],
          ]}
        />
        <P>
          There is no shared backend: the website never sees your data. When you sign in, you are
          signing in to <em>your</em> supervisor.
        </P>
        <H>Who it is for</H>
        <UL
          items={[
            "Developers who use Claude Code and want to see, approve and stop what it does — including from their phone.",
            "Teams shipping agent products who need approvals, a kill switch and an audit trail without building them.",
            "Anyone running agents unattended who needs to know when one is looping, stalled or about to do something irreversible.",
          ]}
        />
        <Note>
          New here? Go to the <Link className="text-ink underline" href="/docs/quickstart">quickstart</Link> — it takes about five minutes.
        </Note>
      </>
    ),
  },
  {
    slug: "quickstart",
    group: "Start here",
    title: "Quickstart",
    summary: "Supervisor running, Claude Code connected, first session on the dashboard.",
    body: (
      <>
        <H>1. Run the supervisor</H>
        <P>Requires Python 3.11 or newer. The first start creates its own database and secret in <C>~/.klaw/supervisor</C>.</P>
        <Code code={`${INSTALL_SUPERVISOR}\n${START_SUPERVISOR}`} />
        <H>2. Create your account</H>
        <P>
          Click <Link className="text-ink underline" href="/login?mode=signup">Get started</Link>. Under the form, the supervisor address
          should read <C>http://127.0.0.1:8000</C>. Sign up with email or Google. If your browser asks to
          let the site access apps on this device, allow it — that is how the page reaches your local supervisor.
        </P>
        <H>3. Connect Claude Code</H>
        <P>In the dashboard open <b>Integrations</b>, choose Claude Code and create a key. Then, in the project you want supervised:</P>
        <Code code={`${INSTALL_COMPANION}\nklaw-companion setup --api-url http://127.0.0.1:8000 --key rl_int_…`} />
        <P>It shows the change to <C>.claude/settings.json</C> and asks before writing. Start Claude Code in that project; the session appears under <b>Sessions</b> within seconds.</P>
        <H>4. Give it some authority</H>
        <P>
          Open <b>Policies</b> and switch to <b>control</b> mode. Risky commands now wait for your approval
          under <b>Approvals</b>, and a call that keeps failing the same way is refused with feedback the agent reads.
        </P>
      </>
    ),
  },
  {
    slug: "install",
    group: "Start here",
    title: "Install the supervisor",
    summary: "Options, data location, configuration, and running it in the background.",
    body: (
      <>
        <H>Install</H>
        <Code code={`${INSTALL_SUPERVISOR}\n${START_SUPERVISOR}`} />
        <P>Prefer isolated tools? <C>pipx install</C> or <C>uv tool install</C> with the same source work too.</P>
        <H>Options</H>
        <Table
          head={["Flag", "Default", "Meaning"]}
          rows={[
            [<C key="a">--port</C>, "8000", "Port to listen on"],
            [<C key="b">--host</C>, "127.0.0.1", "Interface. The default accepts connections from this machine only."],
            [<C key="c">--data-dir</C>, "~/.klaw/supervisor", "Database, secret and config"],
            [<C key="d">--allow-origin</C>, "this website, localhost:3000", "Extra dashboard origins allowed to connect"],
          ]}
        />
        <H>Configuration</H>
        <P>Optional settings go in <C>config.env</C> inside the data directory:</P>
        <Snippet code={"# enable model-based output review and the supervisor chat\nGEMINI_API_KEY=...\n# accept Google sign-in tokens for this client ID\nGOOGLE_CLIENT_IDS=...apps.googleusercontent.com\n# turn off sign-up once your accounts exist\nALLOW_SIGNUP=false"} />
        <P>Your data is one file, <C>klaw.db</C>. Back it up by copying it.</P>
        <Note>Agents on other machines need to reach the supervisor. Run it on a machine they can reach, or expose it with a tunnel (for example Cloudflare's free quick tunnel) and use that address everywhere.</Note>
      </>
    ),
  },
  {
    slug: "claude-code",
    group: "Connect agents",
    title: "Claude Code",
    summary: "One command; what gets sent; what each decision does inside Claude Code.",
    body: (
      <>
        <P>Klaw connects through Claude Code&apos;s own lifecycle hooks, installed per project (or for every project with <C>--user</C>).</P>
        <Code code={`${INSTALL_COMPANION}\nklaw-companion setup --api-url http://127.0.0.1:8000 --key rl_int_…`} />
        <H>What each decision does</H>
        <Table
          head={["Decision", "Before a tool runs", "When Claude wants to finish"]}
          rows={[
            ["Allow", "Nothing — Claude Code's own permissions still apply", "Nothing"],
            ["Warn", "Claude sees the warning as context", "—"],
            ["Block", "The call is denied with the reason", "—"],
            ["Approval", "Held until you answer in the dashboard (or the timeout default)", "—"],
            ["Redirect", "—", "Claude keeps working on the listed gaps (at most N times)"],
            ["Stop", "The turn ends", "The turn ends"],
          ]}
        />
        <H>What is sent</H>
        <UL
          items={[
            "Tool names and inputs, with secret-looking keys redacted — needed for risk rules. Turn off with capture.tool_input in ~/.klaw/config.json.",
            "A fingerprint of each tool result, not the result itself.",
            "Prompts and final messages (each can be turned off).",
            "Token usage, read from Claude Code's local transcript (best effort).",
          ]}
        />
        <H>If the supervisor is down</H>
        <P>The hook exits silently and Claude Code carries on exactly as if it were not installed. Events are queued in <C>~/.klaw/spool.jsonl</C> and delivered later.</P>
        <H>Remove it</H>
        <Code code="klaw-companion claude-code uninstall --project ." />
      </>
    ),
  },
  {
    slug: "openai-agents",
    group: "Connect agents",
    title: "OpenAI Agents SDK",
    summary: "Wrap an existing app in one line.",
    body: (
      <>
        <Code code={INSTALL_SDK} />
        <P>Set two environment variables from the key you created under Integrations:</P>
        <Snippet code={"KLAW_API_URL=http://127.0.0.1:8000\nKLAW_INTEGRATION_KEY=rl_int_..."} />
        <P>Then run your agent through the supervisor:</P>
        <Snippet code={`from klaw.integrations.openai_agents import AgentsSupervisor

supervisor = AgentsSupervisor(app_name="support-bot")
result = await supervisor.run(triage_agent, user_input)   # instead of Runner.run`} />
        <P>Prefer to keep your own <C>Runner.run</C> call?</P>
        <Snippet code={`supervisor.instrument(triage_agent)          # adds the decision guardrail to every tool
result = await Runner.run(triage_agent, user_input, hooks=supervisor.hooks())`} />
        <H>How it plugs in</H>
        <Table
          head={["SDK feature", "Used for"]}
          rows={[
            ["Run hooks", "Pause and stop at every model and tool boundary; usage; handoffs"],
            ["Tool input guardrail", "The decision point: allow, refuse with a message, or wait for approval"],
            ["Tracing", "Observation only: guardrail results and errors"],
          ]}
        />
        <Note>Hosted tools such as web search run on OpenAI&apos;s side: they are observed, not intercepted. An in-flight model call is not interrupted; control applies at the next boundary.</Note>
      </>
    ),
  },
  {
    slug: "python-sdk",
    group: "Connect agents",
    title: "Your own Python agent",
    summary: "Cooperative checkpoints for a custom agent loop.",
    body: (
      <>
        <Code code={INSTALL_SDK_ONLY} />
        <Snippet code={`from klaw import Supervisor

supervisor = Supervisor(api_key="rl_int_...", agent_id="research-agent",
                        api_url="http://127.0.0.1:8000")

@supervisor.monitor(task="Research AI startups")
async def research():
    async with supervisor.tool("search", {"q": "AI startups"}) as call:
        call.set_result(await search("AI startups"))
    async with supervisor.llm("gemini-3.5-flash") as call:
        call.record_usage(prompt_tokens, completion_tokens)`} />
        <P>Every <C>tool()</C> and <C>llm()</C> is a checkpoint where pause, stop and blocked tools take effect.</P>
      </>
    ),
  },
  {
    slug: "dashboard",
    group: "Use it",
    title: "Dashboard tour",
    summary: "What each page is for.",
    body: (
      <Table
        head={["Page", "Use it to"]}
        rows={[
          ["Overview", "See every platform, live sessions, issues and supervisor decisions at a glance"],
          ["Connected agents", "Agents grouped by platform, with reliability scores"],
          ["Live activity", "Watch every event from every agent as it happens"],
          ["Sessions", "Browse every session; open one for its tasks, decisions and full trace"],
          ["Tasks", "One row per objective, with outcome and review"],
          ["Approvals", "Answer agents that are waiting on you"],
          ["Detected issues", "Loops, failures, risky operations and budget breaches, with evidence"],
          ["Interventions", "Every decision the supervisor made, and why"],
          ["Klaw", "Scores and what supervision itself costs"],
          ["Integrations", "Connect environments; keys, setup and limits"],
          ["Policies", "Mode, limits, risk rules, approvals and review"],
        ]}
      />
    ),
  },
  {
    slug: "policies",
    group: "Use it",
    title: "Policies and modes",
    summary: "How much authority the supervisor has, and the rules it applies.",
    body: (
      <>
        <Table
          head={["Mode", "Behaviour"]}
          rows={[
            ["Observe", "Record and report. Nothing about the agent changes."],
            ["Recommend", "Issues are raised and the agent is told what is wrong. Nothing is refused."],
            ["Control", "Block, hold for approval, stop or pause — only actions the policy permits and the platform supports."],
          ]}
        />
        <H>Rules at a decision point</H>
        <UL
          items={[
            "Operator commands first: a stop or a blocked tool from the dashboard.",
            "The policy's never-allowed tools.",
            "Risk rules for destructive and credential-touching operations.",
            "The loop guard: the same call already returned the same result, or failed, N times in this task.",
            "Tools that always need approval.",
            "Optional completion review, which can send work back once with the gaps.",
          ]}
        />
        <P>A policy applies to the whole workspace or to one agent. Unset fields fall through to platform defaults — Claude Code tasks get longer duration limits, for example.</P>
      </>
    ),
  },
  {
    slug: "protocol",
    group: "Reference",
    title: "Event protocol",
    summary: "Connect any platform: two endpoints and a small vocabulary.",
    body: (
      <>
        <P>Adapters send batches to <C>POST /v1/events</C> and ask before acting at <C>POST /v1/decisions</C>. Agents, sessions and tasks are created on first sight.</P>
        <Snippet code={`{
  "event_id": "unique per event (idempotency key)",
  "platform": "claude_code",
  "agent_id": "the platform's identity for the agent",
  "session_id": "the platform's session or run id",
  "event_type": "TOOL_CALL_STARTED",
  "timestamp": "2026-11-13T10:00:00Z",
  "task_id": "optional",
  "execution_context": {"actor": "sub-agent", "cwd": "..."},
  "payload": {"tool_name": "Bash", "tool_call_id": "...", "args_fingerprint": "..."}
}`} />
        <P>Event types: SESSION_STARTED, SESSION_ENDED, PROMPT_SUBMITTED, TASK_CREATED, TASK_COMPLETED, MODEL_CALL, TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED, SUBAGENT_STARTED, SUBAGENT_COMPLETED, AGENT_HANDOFF, AGENT_ERROR, AGENT_IDLE, AGENT_STOPPED, plus control events. Decisions return ALLOW, WARN, REQUEST_APPROVAL, BLOCK, REDIRECT or STOP.</P>
      </>
    ),
  },
  {
    slug: "security",
    group: "Reference",
    title: "Security and privacy",
    summary: "Where data lives, what keys can do, and how failure is handled.",
    body: (
      <UL
        items={[
          "Everything is stored in your supervisor's SQLite file. This website stores only your session token and the supervisor address, in your browser.",
          "Integration keys are scoped: they can report events, request decisions and wait on approvals for their own integration — never read the dashboard or change policy.",
          "Passwords are hashed with scrypt; sessions are signed and expire. Google sign-in tokens are verified against Google's published keys and your configured client ID.",
          "Adapters redact secret-looking keys before sending, and send fingerprints of tool results rather than the results.",
          "The supervisor accepts browser requests only from the dashboards you allow; by default it listens on this machine only.",
          "Every adapter fails open: if the supervisor is unreachable, agents keep working and queue their events.",
        ]}
      />
    ),
  },
  {
    slug: "limitations",
    group: "Reference",
    title: "Limitations",
    summary: "What it cannot do — stated plainly.",
    body: (
      <UL
        items={[
          "Claude Code cannot be paused mid-turn; control applies at the next tool call or at completion.",
          "Repetition inside one shell command is invisible to the loop guard; the unbounded-loop risk rule catches the common shapes.",
          "OpenAI's hosted tools are observed, not intercepted. Only Agents SDK applications are supported, not every app that calls OpenAI.",
          "Supervision runs while your supervisor runs. When it is off, agents work unsupervised and queue their events.",
          "Claude Code token counts come from its local transcript, whose format is not a published API.",
          "Prices for Claude and Gemini 3.x models are not in the price table yet, so their cost shows as unpriced.",
          "One process and SQLite suit a handful of agents; point it at Postgres for a large fleet.",
        ]}
      />
    ),
  },
  {
    slug: "troubleshooting",
    group: "Reference",
    title: "Troubleshooting",
    summary: "The common problems and their fixes.",
    body: (
      <Table
        head={["Symptom", "Fix"]}
        rows={[
          ["Sign-in says it can't reach the supervisor", "Start it (klaw-supervisor start) and allow local network access when the browser asks, or in the site settings next to the address bar."],
          ["Sessions don't appear", "Run klaw-companion doctor in the project. Check ~/.klaw/hook.log."],
          ["Google button is missing", "Add GOOGLE_CLIENT_IDS to the supervisor's config.env and restart it."],
          ["Agents on another machine can't connect", "The supervisor listens on 127.0.0.1 by default. Use a tunnel, or --host 0.0.0.0 on a trusted network."],
        ]}
      />
    ),
  },
];

export function findDoc(slug: string[] | undefined): DocPage | undefined {
  const key = (slug ?? []).join("/");
  return DOCS.find((d) => d.slug === key);
}
