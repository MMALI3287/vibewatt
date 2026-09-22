import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getWrapped } from "../api/client";
import { useFilters } from "../lib/filters";
import { fmtCompact, fmtUsd } from "../lib/format";

export function Wrapped() {
  const [params, setParams] = useSearchParams();
  const [filters] = useFilters();
  const rawYear = params.get("year");
  const year = rawYear ? Number(rawYear) : undefined;
  const valid = year === undefined || (Number.isInteger(year) && year >= 1 && year <= 9998);
  const query = useQuery({ queryKey: ["wrapped", year, filters.source, filters.project, filters.model],
    queryFn: () => getWrapped(year, filters), enabled: valid });
  if (!valid) return <section className="state" role="alert"><h1>Invalid year</h1><p>Choose a year from 1 to 9998.</p>
    <button onClick={() => { const next = new URLSearchParams(params); next.delete("year"); setParams(next); }}>Use current year</button></section>;
  if (query.isPending) return <section className="state" aria-busy="true">Loading year in review…</section>;
  if (query.isError) return <section className="state state-error" role="alert"><h1>Could not load Wrapped</h1>
    <p>{query.error.message}</p><button onClick={() => query.refetch()}>Retry Wrapped</button></section>;
  const data = query.data;
  function download() {
    const lines = [`vibewatt · ${data.year}`, `${fmtCompact(data.stored_tokens)} tokens`,
      `${fmtUsd(data.stored_cost_usd)} API-equivalent cost`, `${data.stored_sessions} stored sessions`,
      `${data.longest_streak} day longest streak`, "Retained local + harvested usage. Not account-wide.",
      data.unpriced_turns ? `${data.unpriced_turns} unpriced responses excluded from cost.` : "Cost is not a subscription bill."];
    const escape = (s: string) => s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]!);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="440" viewBox="0 0 900 440"><rect width="900" height="440" fill="#fcfcfb"/>${lines.map((line, index) => `<text x="40" y="${65 + index * 50}" fill="#0b0b0b" font-family="sans-serif" font-size="${index ? 22 : 32}">${escape(line)}</text>`).join("")}</svg>`;
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    const link = document.createElement("a"); link.href = url; link.download = `vibewatt-wrapped-${data.year}.svg`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="wrapped-page">
    <h1>{data.year} Wrapped</h1>
    <label>Year <input aria-label="Wrapped year" type="number" min="1" max="9998" value={data.year}
      onChange={event => { if (event.target.value) { const next = new URLSearchParams(params); next.set("year", event.target.value); setParams(next); } }} /></label>
    <p>The year uses {data.timezone}. Source, project and model filters apply. Date-range filters are replaced by the selected calendar year.</p>
    {data.stored_sessions === 0 && <p role="status">No stored usage for this year and selection. Sync local logs or choose another year.</p>}
    {data.unpriced_turns > 0 && <p className="notice">{data.unpriced_turns} unpriced responses are excluded from cost. Cost totals are incomplete.</p>}
    <section className="card" aria-label="Year figures">
      <h2>Retained local and harvested usage</h2><p>Not account-wide. API-equivalent cost is not your subscription bill.</p>
      <dl className="finding-metrics">
        <div><dt>Stored API-equivalent cost</dt><dd>{fmtUsd(data.stored_cost_usd)}</dd></div>
        <div><dt>Harvested portion</dt><dd>{fmtUsd(data.harvested_cost_usd)}</dd></div>
        <div><dt>Stored tokens</dt><dd>{fmtCompact(data.stored_tokens)}</dd></div>
        <div><dt>Stored sessions</dt><dd>{data.stored_sessions}</dd></div>
        <div><dt>Live local-log cost</dt><dd>{fmtUsd(data.local_summary.total.cost_usd)}</dd></div>
        <div><dt>API-equivalent / annual plan</dt><dd>{data.api_equivalent_multiple === null ? "Unavailable" : `${data.api_equivalent_multiple.toFixed(2)}×`}</dd></div>
        <div><dt>Busiest day by tokens</dt><dd>{data.busiest_day ?? "Unavailable"}</dd></div>
        <div><dt>Busiest local hour</dt><dd>{data.busiest_hour === null ? "Unavailable" : `${data.busiest_hour}:00`}</dd></div>
        <div><dt>Longest observed streak</dt><dd>{data.longest_streak} days</dd></div>
        <div><dt>Local cache-read savings</dt><dd>{data.cache_savings_usd === null ? "Unavailable: unpriced model" : fmtUsd(data.cache_savings_usd)}</dd></div>
      </dl>
      {data.biggest_session && <p>Biggest session by tokens: {data.biggest_session.title} · {fmtCompact(data.biggest_session.tokens)} tokens · {fmtUsd(data.biggest_session.cost_usd)} ({data.biggest_session.harvested ? "harvested" : "local"}).</p>}
      <button onClick={download}>Download share card</button><p>The card contains aggregate figures only, without project names or session titles.</p>
    </section>
    <section className="card"><h2>Top projects by tokens</h2><div className="table-wrap"><table><thead><tr><th>Project</th><th>Tokens</th><th>Cost</th></tr></thead>
      <tbody>{data.top_projects.map((p, index) => <tr key={index}><td>{p.name}</td><td>{fmtCompact(p.tokens)}</td><td>{fmtUsd(p.cost_usd)}</td></tr>)}</tbody></table></div></section>
    <section className="card"><h2>Model mix over time</h2><div className="table-wrap"><table><thead><tr><th>Month</th><th>Model</th><th>Tokens</th><th>Cost</th></tr></thead>
      <tbody>{data.model_months.map(p => <tr key={`${p.month}|${p.model}`}><td>{p.month}</td><td>{p.model}</td><td>{fmtCompact(p.tokens)}</td><td>{fmtUsd(p.cost_usd)}</td></tr>)}</tbody></table></div></section>
    <details className="card"><summary>Coverage and interpretation</summary>{data.notes.map(note => <p key={note}>{note}</p>)}</details>
  </section>;
}
