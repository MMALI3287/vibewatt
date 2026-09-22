import { useQuery } from "@tanstack/react-query";
import { getSummary, type Bucket, type Summary } from "../api/client";
import { Hero } from "../components/Hero";
import { PlanMeters } from "../components/PlanMeters";
import { Reconciliation } from "../components/Reconciliation";
import { UsageCharts } from "../components/UsageCharts";
import { DEFAULT_FILTERS, serializeFilters, useFilters, type Metric } from "../lib/filters";
import { bucketTokens, fmtCompact, fmtInt, fmtPct, fmtUsd } from "../lib/format";
import { useTitle } from "../lib/title";
import { barLayout } from "../lib/chart";
import { DataTable } from "../components/DataTable";

const value = (b: Bucket, m: Metric) => (m === "tokens" ? bucketTokens(b) : b.cost_usd);
const fmtValue = (v: number, m: Metric) => (m === "tokens" ? fmtCompact(v) : fmtUsd(v));

export function Overview() {
  useTitle("Overview");
  const [filters, update] = useFilters();
  const summary = useQuery({ queryKey: ["summary", filters], queryFn: () => getSummary(filters) });
  const unfiltered = useQuery({
    queryKey: ["summary", DEFAULT_FILTERS],
    queryFn: () => getSummary(DEFAULT_FILTERS),
  });
  const isFiltered = serializeFilters({ ...filters, metric: "cost" }).toString() !== "";
  const historyOnlyDays = unfiltered.data?.restored_days.length ?? 0;

  // Plan utilization is account-wide, so it shows whatever the local summary does.
  const meters = (
    <>
      <h1>Overview</h1>
      <section className="plan" aria-label="Plan utilization">
        <PlanMeters />
      </section>
    </>
  );
  if (summary.isPending) {
    return (
      <>
        {meters}
        <section className="state" aria-busy="true">
          <p>Loading usage…</p>
        </section>
      </>
    );
  }
  if (summary.isError) {
    return (
      <>
        {meters}
        <section className="state state-error" role="alert">
          <h1>Could not load usage</h1>
          <p>{summary.error.message}</p>
          <button type="button" onClick={() => summary.refetch()}>
            Retry
          </button>
        </section>
      </>
    );
  }

  const s = summary.data;
  if (s.total.responses === 0) {
    return (
      <>
        {meters}
        <section className="state">
          <h1>No usage for these filters</h1>
          <p>Nothing in the local store matches. Widen the range, reset filters, or run a sync.</p>
          <button type="button" onClick={() => update(DEFAULT_FILTERS)}>
            Reset filters
          </button>
        </section>
      </>
    );
  }

  return (
    <>
      {meters}
      <Hero summary={s} filters={filters} />
      {isFiltered && historyOnlyDays > 0 && (
        <p className="notice" role="status">
          Filtered views exclude {historyOnlyDays} day{historyOnlyDays === 1 ? "" : "s"} known only from the
          imported history.json, whose logs were deleted by retention.
        </p>
      )}
      {s.unknown_models.length > 0 && (
        <p className="notice" role="status">
          Unpriced models excluded from cost: {s.unknown_models.join(", ")}
        </p>
      )}
      <Kpis s={s} />
      <Reconciliation filters={filters} />
      <DailyChart s={s} metric={filters.metric} />
      <ModelTable s={s} metric={filters.metric} />
      <UsageCharts summary={s} filters={filters} />
    </>
  );
}

export function Kpis({ s }: { s: Summary }) {
  const items = [
    { label: "Cost", value: fmtUsd(s.total.cost_usd) },
    { label: "Tokens", value: fmtCompact(bucketTokens(s.total)) },
    { label: "Responses", value: fmtInt(s.total.responses) },
    { label: "Sessions", value: fmtInt(s.sessions) },
    { label: "Cache hit rate", value: fmtPct(s.cache_hit_rate) },
  ];
  return (
    <section className="kpis" aria-label="Key figures (local logs)">
      {items.map((k) => (
        <div className="kpi" key={k.label}>
          <span className="kpi-label">{k.label}</span>
          <span className="kpi-value" data-testid={`kpi-${k.label.toLowerCase().replace(/ /g, "-")}`}>
            {k.value}
          </span>
        </div>
      ))}
    </section>
  );
}

function DailyChart({ s, metric }: { s: Summary; metric: Metric }) {
  const days = Object.keys(s.by_day).sort();
  const values = days.map((d) => value(s.by_day[d], metric));
  const peak = Math.max(...values, 0);
  const w = 720;
  const h = 180;
  const { barWidth: barW, gap } = barLayout(days.length, w);
  const title = `Daily ${metric === "tokens" ? "tokens" : "cost"}`;

  return (
    <section className="card" aria-label={title}>
      <h2>{title}</h2>
      <div className="chart">
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={title}>
          <line x1="0" y1={h - 0.5} x2={w} y2={h - 0.5} className="axis" />
          {days.map((d, i) => {
            const bh = peak ? (values[i] / peak) * (h - 4) : 0;
            return (
              <rect key={d} x={i * (barW + gap)} y={h - bh} width={barW} height={bh} className="bar">
                <title>{`${d}: ${fmtValue(values[i], metric)}`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
      <details>
        <summary>Table</summary>
        <DataTable label="Daily totals" rows={days.map((d, i) => ({ day: d, value: values[i], responses: s.by_day[d].responses })).reverse()} columns={[
          { accessorKey: "day", header: "Day" },
          { accessorKey: "value", header: metric === "tokens" ? "Tokens" : "Cost", cell: (info) => fmtValue(info.row.original.value, metric) },
          { accessorKey: "responses", header: "Responses", cell: (info) => fmtInt(info.row.original.responses) },
        ]} />
      </details>
    </section>
  );
}

function ModelTable({ s, metric }: { s: Summary; metric: Metric }) {
  const rows = Object.entries(s.by_model)
    .sort((a, b) => value(b[1], metric) - value(a[1], metric))
    .map(([name, b]) => ({ name, cost: b.cost_usd, tokens: bucketTokens(b), responses: b.responses }));
  return (
    <section className="card" aria-label="By model">
      <h2>By model</h2>
      <DataTable label="By model" rows={rows} columns={[
        { accessorKey: "name", header: "Model" },
        { accessorKey: "cost", header: "Cost", cell: (info) => fmtUsd(info.row.original.cost) },
        { accessorKey: "tokens", header: "Tokens", cell: (info) => fmtCompact(info.row.original.tokens) },
        { accessorKey: "responses", header: "Responses", cell: (info) => fmtInt(info.row.original.responses) },
      ]} />
    </section>
  );
}
