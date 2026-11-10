"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Execution } from "@/lib/api";
import { Button, Card, CardHeader, EmptyState, Table, Td, Th } from "./ui";

/*
 * Series order is the stacking order, bottom to top. Green and red are never
 * adjacent in the stack, which is what keeps the pair readable under
 * deuteranopia; the set was validated against the panel surface for the
 * lightness band, chroma floor, CVD separation, normal-vision floor and
 * contrast.
 */
const SERIES = [
  { key: "completed", label: "Completed", color: "var(--success)" },
  { key: "active", label: "Active", color: "var(--running)" },
  { key: "failed", label: "Failed", color: "var(--failed)" },
  { key: "cancelled", label: "Cancelled", color: "var(--cancelled)" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];
type Bucket = { hour: number; label: string; total: number } & Record<SeriesKey, number>;

function bucketise(executions: Execution[], hours: number): Bucket[] {
  const now = new Date();
  now.setMinutes(0, 0, 0);
  const buckets: Bucket[] = [];
  for (let offset = hours - 1; offset >= 0; offset -= 1) {
    const slot = new Date(now.getTime() - offset * 3600_000);
    buckets.push({
      hour: slot.getTime(),
      label: slot.toLocaleTimeString(undefined, { hour: "2-digit", hour12: false }),
      completed: 0,
      active: 0,
      failed: 0,
      cancelled: 0,
      total: 0,
    });
  }

  const first = buckets[0]?.hour ?? now.getTime();
  for (const execution of executions) {
    const started = new Date(execution.started_at).getTime();
    if (Number.isNaN(started) || started < first) continue;
    const index = Math.min(
      buckets.length - 1,
      Math.floor((started - first) / 3600_000),
    );
    const bucket = buckets[index];
    if (!bucket) continue;
    const key: SeriesKey =
      execution.status === "completed"
        ? "completed"
        : execution.status === "failed"
          ? "failed"
          : execution.status === "cancelled"
            ? "cancelled"
            : "active";
    bucket[key] += 1;
    bucket.total += 1;
  }
  return buckets;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ dataKey: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((entry) => entry.value > 0);
  const total = payload.reduce((sum, entry) => sum + (entry.value ?? 0), 0);
  return (
    <div className="rounded-md border border-line-strong bg-panel-raised px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-ink">{label}:00</div>
      {rows.length === 0 ? (
        <div className="text-muted">No executions</div>
      ) : (
        rows.map((entry) => {
          const series = SERIES.find((item) => item.key === entry.dataKey);
          return (
            <div key={entry.dataKey} className="flex items-center gap-2 py-0.5">
              <span
                className="size-2 rounded-[2px]"
                style={{ backgroundColor: entry.color }}
              />
              <span className="flex-1 text-muted">{series?.label ?? entry.dataKey}</span>
              <span className="tabular-nums text-ink">{entry.value}</span>
            </div>
          );
        })
      )}
      {total > 0 ? (
        <div className="mt-1 border-t border-line pt-1 text-muted">
          total <span className="tabular-nums text-ink">{total}</span>
        </div>
      ) : null}
    </div>
  );
}

export function ExecutionVolumeChart({
  executions,
  hours = 24,
}: {
  executions: Execution[];
  hours?: number;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const data = useMemo(() => bucketise(executions, hours), [executions, hours]);
  const total = data.reduce((sum, bucket) => sum + bucket.total, 0);

  return (
    <Card>
      <CardHeader
        title="Execution volume"
        description={`Executions started in the last ${hours} hours, by outcome.`}
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setView(view === "chart" ? "table" : "chart")}
            aria-label={view === "chart" ? "Show as table" : "Show as chart"}
          >
            {view === "chart" ? "Table" : "Chart"}
          </Button>
        }
      />

      {total === 0 ? (
        <EmptyState
          title="No executions in this window"
          description="Run a demo agent to populate the timeline: python demo/run_demo.py healthy"
        />
      ) : view === "table" ? (
        <Table>
          <thead>
            <tr>
              <Th>Hour</Th>
              {SERIES.map((series) => (
                <Th key={series.key} className="text-right">
                  {series.label}
                </Th>
              ))}
              <Th className="text-right">Total</Th>
            </tr>
          </thead>
          <tbody>
            {data
              .filter((bucket) => bucket.total > 0)
              .map((bucket) => (
                <tr key={bucket.hour}>
                  <Td className="text-muted">{bucket.label}:00</Td>
                  {SERIES.map((series) => (
                    <Td key={series.key} className="text-right tabular-nums">
                      {bucket[series.key] || "—"}
                    </Td>
                  ))}
                  <Td className="text-right tabular-nums text-ink">{bucket.total}</Td>
                </tr>
              ))}
          </tbody>
        </Table>
      ) : (
        <div className="p-4">
          {/* Legend sits above the plot: identity is never carried by colour alone. */}
          <div className="mb-3 flex flex-wrap items-center gap-4">
            {SERIES.map((series) => (
              <div key={series.key} className="flex items-center gap-1.5 text-xs text-muted">
                <span
                  className="size-2 rounded-[2px]"
                  style={{ backgroundColor: series.color }}
                />
                {series.label}
              </div>
            ))}
          </div>

          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
                <CartesianGrid
                  vertical={false}
                  stroke="var(--border)"
                  strokeDasharray="2 4"
                />
                <XAxis
                  dataKey="label"
                  interval={3}
                  tick={{ fill: "var(--text-faint)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  width={44}
                  tick={{ fill: "var(--text-faint)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  content={<ChartTooltip />}
                  cursor={{ fill: "var(--panel-raised)" }}
                />
                {SERIES.map((series) => (
                  <Bar
                    key={series.key}
                    dataKey={series.key}
                    stackId="executions"
                    fill={series.color}
                    // A 1px surface stroke renders as a 2px gap between
                    // adjacent fills, so stacked segments stay separable.
                    stroke="var(--panel)"
                    strokeWidth={1}
                    radius={[2, 2, 0, 0]}
                    maxBarSize={28}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </Card>
  );
}
