"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckSquare,
  Gauge,
  Hand,
  LayoutDashboard,
  ListChecks,
  LogOut,
  MessagesSquare,
  Plug,
  Radio,
  Settings,
  ShieldCheck,
} from "lucide-react";
import type { ReactNode } from "react";
import { useLive } from "@/lib/live";
import { useAsync } from "@/lib/hooks";
import { api, clearToken } from "@/lib/api";
import { cn } from "./ui";
import { BackendStatus } from "./backend-status";

const NAV_GROUPS = [
  {
    label: "Monitor",
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { href: "/agents", label: "Connected agents", icon: Bot },
      { href: "/activity", label: "Live activity", icon: Radio },
      { href: "/monitor", label: "Sessions", icon: Activity },
      { href: "/tasks", label: "Tasks", icon: ListChecks },
    ],
  },
  {
    label: "Supervise",
    items: [
      { href: "/approvals", label: "Approvals", icon: Hand },
      { href: "/incidents", label: "Detected issues", icon: AlertTriangle },
      { href: "/interventions", label: "Interventions", icon: CheckSquare },
      { href: "/reliability", label: "Reliability", icon: Gauge },
      { href: "/supervisor", label: "Ask the supervisor", icon: MessagesSquare },
    ],
  },
  {
    label: "Configure",
    items: [
      { href: "/integrations", label: "Integrations", icon: Plug },
      { href: "/policies", label: "Policies", icon: ShieldCheck },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

function ConnectionPill() {
  const { status } = useLive();
  const map = {
    open: { color: "var(--success)", label: "live" },
    connecting: { color: "var(--warning)", label: "connecting" },
    closed: { color: "var(--failed)", label: "offline" },
    unauthorized: { color: "var(--failed)", label: "no token" },
  } as const;
  const { color, label } = map[status];
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-muted" title={`WebSocket ${status}`}>
      <span
        className={cn("size-1.5 rounded-full", status === "open" && "pulse")}
        style={{ backgroundColor: color }}
      />
      {label}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const incidents = useAsync(() => api.incidents({ status: "open" }), [], {
    intervalMs: 20000,
  });
  const openCount = incidents.data?.length ?? 0;
  const approvals = useAsync(() => api.approvals({ status: "pending" }), [], {
    intervalMs: 5000,
  });
  const pendingApprovals = approvals.data?.length ?? 0;
  const me = useAsync(() => api.me(), []);
  const badges: Record<string, { count: number; tone: string }> = {
    "/incidents": { count: openCount, tone: "var(--failed)" },
    "/approvals": { count: pendingApprovals, tone: "var(--warning)" },
  };

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-panel md:flex">
        <div className="flex items-center gap-2 border-b border-line px-4 py-4">
          <div className="grid size-6 place-items-center rounded bg-[var(--accent)] text-[11px] font-bold text-white">
            R
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-ink">Reliability</div>
            <div className="truncate text-[10px] text-faint">agent control plane</div>
          </div>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto p-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="px-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-faint">
                {group.label}
              </div>
              <div className="space-y-0.5">
                {group.items.map(({ href, label, icon: Icon }) => {
                  const active = pathname === href || pathname.startsWith(`${href}/`);
                  const badge = badges[href];
                  return (
                    <Link
                      key={href}
                      href={href}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                        active
                          ? "bg-panel-raised text-ink"
                          : "text-muted hover:bg-panel-raised hover:text-ink",
                      )}
                    >
                      <Icon className="size-4" strokeWidth={1.75} />
                      <span className="flex-1">{label}</span>
                      {badge && badge.count > 0 ? (
                        <span
                          className="rounded px-1.5 text-[10px] font-medium"
                          style={{ color: badge.tone, backgroundColor: `color-mix(in srgb, ${badge.tone} 15%, transparent)` }}
                        >
                          {badge.count}
                        </span>
                      ) : null}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="space-y-2 border-t border-line px-4 py-3">
          <div className="flex items-center justify-between">
            <ConnectionPill />
            <button
              type="button"
              title="Sign out"
              className="text-faint hover:text-ink"
              onClick={() => {
                clearToken();
                window.location.replace("/login");
              }}
            >
              <LogOut className="size-3.5" />
            </button>
          </div>
          {me.data ? <div className="truncate text-[11px] text-faint">{me.data.email}</div> : null}
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <BackendStatus />
        {children}
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
}) {
  return (
    <header className="border-b border-line px-6 py-5">
      {breadcrumb ? <div className="mb-1.5 text-xs text-faint">{breadcrumb}</div> : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-ink">{title}</h1>
          {description ? (
            <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}

export function PageBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("space-y-5 p-6", className)}>{children}</div>;
}
