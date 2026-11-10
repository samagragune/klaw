"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  Bot,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  ShieldCheck,
} from "lucide-react";
import type { ReactNode } from "react";
import { useLive } from "@/lib/live";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "./ui";

const NAV = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/monitor", label: "Live Monitor", icon: Activity },
  { href: "/incidents", label: "Incidents", icon: AlertTriangle },
  { href: "/supervisor", label: "Supervisor", icon: MessagesSquare },
  { href: "/policies", label: "Policies", icon: ShieldCheck },
  { href: "/settings", label: "Settings", icon: Settings },
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

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex items-center gap-2 border-b border-line px-4 py-4">
          <div className="grid size-6 place-items-center rounded bg-[var(--accent)] text-[11px] font-bold text-white">
            R
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-ink">Reliability</div>
            <div className="truncate text-[10px] text-faint">agent control plane</div>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 p-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
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
                {href === "/incidents" && openCount > 0 ? (
                  <span className="rounded bg-[var(--failed)]/15 px-1.5 text-[10px] font-medium text-[var(--failed)]">
                    {openCount}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-line px-4 py-3">
          <ConnectionPill />
        </div>
      </aside>

      <main className="min-w-0 flex-1">{children}</main>
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
