import { useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getBlocks, type Summary } from "../api/client";
import { type Filters, type Metric } from "../lib/filters";
import { bucketTokens, fmtCompact, fmtUsd } from "../lib/format";
import { DataTable } from "./DataTable";

const format = (value: number, metric: Metric) => metric === "tokens" ? fmtCompact(value) : fmtUsd(value);

export function UsageCharts({ summary, filters }: { summary: Summary; filters: Filters }) {
  const metric = filters.metric;
  const daily = Object.entries(summary.by_day).sort(([a], [b]) => a.localeCompare(b))
    .map(([day, bucket]) => ({ day, value: metric === "tokens" ? bucketTokens(bucket) : bucket.cost_usd }));
  const days: { day: string; value: number }[] = [];
  // UTC is only calendar arithmetic here; API day keys already use the report timezone.
  if (daily.length) {
    const values = new Map(daily.map(d => [d.day, d.value]));
    const start = new Date(`${daily[0].day}T00:00:00Z`);
    const end = new Date(`${daily[daily.length - 1].day}T00:00:00Z`);
    for (let time = start.getTime(); time <= end.getTime(); time += 86400000) {
      const day = new Date(time).toISOString().slice(0, 10);
      days.push({ day, value: values.get(day) ?? 0 });
    }
  }
  const peak = Math.max(0, ...daily.map(d => d.value));
  const hours = Array.from({ length: 24 }, (_, hour) => {
    const bucket = summary.by_hour[String(hour)];
    return { hour: `${String(hour).padStart(2, "0")}:00`, value: bucket ? metric === "tokens" ? bucketTokens(bucket) : bucket.cost_usd : 0 };
  });
  return <>
    <section className="card" aria-label={`Daily ${metric} heatmap`}>
      <h2>Daily {metric} heatmap · local logs</h2>
      <p className="muted">Use Metric above to switch cost and tokens. Each column is a week, Sunday first. Stronger blue means more usage.</p>
      {!days.length ? <p>No daily usage for these filters.</p> : <>
        <Heatmap days={days} peak={peak} metric={metric} />
        <p className="muted">{days[0].day} to {days[days.length - 1].day}</p>
        <details><summary>Heatmap table</summary><DataTable rows={days} label="Daily usage" columns={[
          { accessorKey: "day", header: "Day" },
          { accessorKey: "value", header: metric === "tokens" ? "Tokens" : "Cost", cell: info => format(info.row.original.value, metric) },
        ]} /></details>
      </>}
    </section>
    <section className="card" aria-label="Hour-of-day usage">
      <h2>Hour-of-day {metric} · local logs</h2>
      <p className="muted">Hours use the report timezone. Retained daily rollups do not contain hourly detail.</p>
      <div className="hour-chart" role="img"
        aria-label={`Hour-of-day ${metric} bar chart. Values are in the Hourly table below.`}>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={hours} margin={{ left: 0, right: 10, top: 10, bottom: 0 }}>
            <XAxis dataKey="hour" stroke="var(--ts)" minTickGap={28} />
            <YAxis stroke="var(--ts)" tickFormatter={v => format(Number(v), metric)} width={64} />
            <Tooltip formatter={v => format(Number(v), metric)} cursor={{ fill: "var(--s2)" }}
              contentStyle={{ background: "var(--s0)", color: "var(--tp)", borderColor: "var(--bd)" }}
              itemStyle={{ color: "var(--tp)" }} labelStyle={{ color: "var(--tp)" }} />
            <Bar dataKey="value" name={metric === "tokens" ? "Tokens" : "Cost"} fill="var(--l4)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <details><summary>Hourly table</summary><DataTable rows={hours} label="Hourly usage" columns={[
        { accessorKey: "hour", header: "Hour" },
        { accessorKey: "value", header: metric === "tokens" ? "Tokens" : "Cost", cell: info => format(info.row.original.value, metric) },
      ]} /></details>
    </section>
    <Blocks filters={filters} />
  </>;
}

/**
 * One tab stop for the whole grid, arrow keys between days (A-040). Each column
 * is a week, so Left and Right move a week and Up and Down a day.
 */
function Heatmap({ days, peak, metric }: { days: { day: string; value: number }[]; peak: number; metric: Metric }) {
  const [active, setActive] = useState(days.length - 1);
  const cells = useRef<(HTMLSpanElement | null)[]>([]);
  const move = (event: KeyboardEvent) => {
    const step = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1, Home: -Infinity, End: Infinity }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const next = Math.min(days.length - 1, Math.max(0, active + step));
    setActive(next);
    cells.current[next]?.focus();
  };
  const current = Math.min(active, days.length - 1);
  return <div className="heatmap-wrap">
    <div className="heatmap" role="grid" aria-label={`Daily ${metric}, one cell per day. Use arrow keys to move between days.`} onKeyDown={move}>
      <div role="row" className="heatmap-row">
        {days.map((row, index) => {
          const level = row.value && peak ? Math.max(1, Math.ceil(5 * (row.value / peak) ** 0.25)) : 0;
          return <span key={row.day} ref={el => { cells.current[index] = el; }} role="gridcell"
            tabIndex={index === current ? 0 : -1} className="heat-cell" onFocus={() => setActive(index)} style={{
              background: `var(--l${level})`,
              gridRow: index === 0 ? new Date(`${row.day}T00:00:00Z`).getUTCDay() + 1 : undefined,
            }} aria-label={`${row.day}: ${format(row.value, metric)}`}>
            <span role="tooltip">{row.day}: {format(row.value, metric)}</span>
          </span>;
        })}
      </div>
    </div>
  </div>;
}

function Blocks({ filters }: { filters: Filters }) {
  const query = useQuery({ queryKey: ["blocks", filters], queryFn: () => getBlocks(filters) });
  const rows = [...(query.data ?? [])].sort((a, b) => Number(b.is_active) - Number(a.is_active) || b.start.localeCompare(a.start));
  return <section className="card" aria-label="Usage blocks">
    <h2>Usage blocks · local logs</h2>
    <p className="muted">Estimated windows from observed responses. These are not account-wide quota windows.</p>
    {query.isPending && <p role="status">Loading blocks…</p>}
    {query.isError && <p role="alert">Could not load blocks. <button onClick={() => query.refetch()}>Retry</button></p>}
    {query.isSuccess && <DataTable rows={rows} label="Blocks" columns={[
      { accessorKey: "is_active", header: "Status", cell: info => info.row.original.is_active ? "Active" : "Ended" },
      { accessorKey: "start", header: "Start" }, { accessorKey: "end", header: "End" },
      { accessorKey: "tokens", header: "Tokens", cell: info => fmtCompact(info.row.original.tokens) },
      { accessorKey: "cost_usd", header: "Cost", cell: info => fmtUsd(info.row.original.cost_usd) },
      { accessorKey: "tokens_per_minute", header: "Tokens / minute", cell: info => fmtCompact(info.row.original.tokens_per_minute) },
      { accessorFn: row => row.models.join(", "), id: "models", header: "Models" },
    ]} />}
  </section>;
}
