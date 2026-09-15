import { useQuery } from "@tanstack/react-query";
import { getSummary, type Bucket, type Summary } from "../api/client";
import { Hero } from "../components/Hero";
import { DEFAULT_FILTERS, serializeFilters, useFilters, type Metric } from "../lib/filters";
import { bucketTokens, fmtCompact, fmtInt, fmtPct, fmtUsd } from "../lib/format";

const value = (b: Bucket, m: Metric) => (m === "tokens" ? bucketTokens(b) : b.cost_usd);
const fmtValue = (v: number, m: Metric) => (m === "tokens" ? fmtCompact(v) : fmtUsd(v));

export function Overview() {
  const [filters, update] = useFilters();
  const summary = useQuery({ queryKey: ["summary", filters], queryFn: () => getSummary(filters) });
  const unfiltered = useQuery({
    queryKey: ["summary", DEFAULT_FILTERS],
    queryFn: () => getSummary(DEFAULT_FILTERS),
  });
  const isFiltered = serializeFilters({ ...filters, metric: "cost" }).toString() !== "";
  const historyOnlyDays = unfiltered.data?.restored_days.length ?? 0;

  if (summary.isPending) {
    return (
      <section className="state" aria-busy="true">
        <p>Loading usage…</p>
      </section>
    );
  }
  if (summary.isError) {
    return (
      <section className="state state-error" role="alert">
        <h1>Could not load usage</h1>
        <p>{summary.error.message}</p>
        <button type="button" onClick={() => summary.refetch()}>
          Retry
        </button>
      </section>
    );
  }

  const s = summary.data;
  if (s.total.responses === 0) {
    return (
      <section className="state">
        <h1>No usage for these filters</h1>
        <p>Nothing in the local store matches. Widen the range, reset filters, or run a sync.</p>
        <button type="button" onClick={() => update(DEFAULT_FILTERS)}>
          Reset filters
        </button>
      </section>
    );
  }

  return (
    <>
      <Hero summary={s} filters={filters} />
      {isFiltered && historyOnlyDays > 0 && (
        <p className="notice" role="status">
          Filtered views exclude {historyOnlyDays} day{historyOnlyDays === 1 ? "" : "s"} kept only in the
          history rollup, whose logs were deleted by retention.
        </p>
      )}
      {s.unknown_models.length > 0 && (
        <p className="notice" role="status">
          Unpriced models excluded from cost: {s.unknown_models.join(", ")}
        </p>
      )}
      <Kpis s={s} />
      <DailyChart s={s} metric={filters.metric} />
      <ModelTable s={s} metric={filters.metric} />
    </>
  );
}

function Kpis({ s }: { s: Summary }) {
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
  const gap = 2;
  const barW = days.length ? Math.max(1, (w - gap * (days.length - 1)) / days.length) : 0;
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
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Day</th>
                <th className="num">{metric === "tokens" ? "Tokens" : "Cost"}</th>
                <th className="num">Responses</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d, i) => (
                <tr key={d}>
                  <td>{d}</td>
                  <td className="num">{fmtValue(values[i], metric)}</td>
                  <td className="num">{fmtInt(s.by_day[d].responses)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

function ModelTable({ s, metric }: { s: Summary; metric: Metric }) {
  const rows = Object.entries(s.by_model).sort((a, b) => value(b[1], metric) - value(a[1], metric));
  return (
    <section className="card" aria-label="By model">
      <h2>By model</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">Cost</th>
              <th className="num">Tokens</th>
              <th className="num">Responses</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, b]) => (
              <tr key={name}>
                <td>{name}</td>
                <td className="num">{fmtUsd(b.cost_usd)}</td>
                <td className="num">{fmtCompact(bucketTokens(b))}</td>
                <td className="num">{fmtInt(b.responses)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
