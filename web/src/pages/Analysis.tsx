import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { dismissFinding, getFindings, type Finding } from "../api/client";
import { useFilters } from "../lib/filters";
import { fmtUsd } from "../lib/format";

const KINDS: { kind: Finding["kind"]; label: string }[] = [
  { kind: "anomaly", label: "Cost anomalies" },
  { kind: "cache", label: "Cache opportunities" },
  { kind: "tip", label: "Ranked advice" },
  { kind: "waste", label: "Token-waste checks" },
  { kind: "peak", label: "Peak windows" },
  { kind: "context", label: "Context windows" },
];

export function Analysis() {
  const [filters] = useFilters();
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [severity, setSeverity] = useState("all");
  const query = useQuery({
    queryKey: ["analysis", filters, includeDismissed],
    queryFn: () => getFindings(filters, includeDismissed),
  });
  if (query.isPending) return <section className="state" aria-busy="true">Analyzing stored usage…</section>;
  if (query.isError) return (
    <section className="state state-error" role="alert">
      <h1>Could not load analysis</h1><p>{query.error.message}</p>
      <button onClick={() => query.refetch()}>Retry</button>
    </section>
  );
  const rows = query.data.findings.filter(f => severity === "all" || f.severity === severity);
  return (
    <section className="analysis-page">
      <div className="analysis-heading">
        <div><h1>Analysis</h1><p>Evidence and suggestions from your stored usage.</p></div>
        <button onClick={() => query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Refreshing…" : "Refresh analysis"}
        </button>
      </div>
      <div className="analysis-controls">
        <label>Severity <select aria-label="Severity" value={severity} onChange={e => setSeverity(e.target.value)}>
          <option value="all">All severities</option><option value="urgent">Urgent</option>
          <option value="warning">Warning</option><option value="info">Info</option>
        </select></label>
        <label><input type="checkbox" checked={includeDismissed}
          onChange={e => setIncludeDismissed(e.target.checked)} /> Show dismissed</label>
      </div>
      <details className="card analysis-coverage">
        <summary>Coverage and interpretation</summary>
        {query.data.notes.map(note => <p key={note}>{note}</p>)}
      </details>
      {rows.length === 0 && <div className="state" role="status">
        <h2>No findings for this selection</h2>
        <p>This can mean no rule triggered or there is not enough evidence. Check coverage, widen the filters or sync more data.</p>
      </div>}
      {KINDS.map(({ kind, label }) => {
        const group = rows.filter(f => f.kind === kind);
        return (
          <details className="card analysis-group" key={kind}>
            <summary>{label} <span className="finding-count">{group.length}</span></summary>
            {group.length === 0 ? <p>No matching findings.</p> :
              group.map(f => <FindingCard key={f.id} finding={f} />)}
          </details>
        );
      })}
    </section>
  );
}

function FindingCard({ finding: f }: { finding: Finding }) {
  const location = useLocation();
  const qc = useQueryClient();
  const dismiss = useMutation({
    mutationFn: () => dismissFinding(f.id, !f.dismissed),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["analysis"] }),
  });
  const scope = f.coverage === "account" ? "Account-wide" : f.coverage === "harvested" ? "Harvested snapshot" : "Local logs only";
  return (
    <article className="finding" aria-label={f.title}>
      <div className="finding-heading">
        <h2>{f.title}</h2><span className={`severity severity-${f.severity}`}>{f.severity}</span>
        {f.dismissed && <span className="finding-status">Dismissed</span>}
      </div>
      <p className="finding-meta">{scope}{f.day ? ` · ${f.day}` : ""}</p>
      <p>{f.detail}</p>
      {f.savings_usd !== null && <p className="finding-saving">Estimated potential saving: {fmtUsd(f.savings_usd)}</p>}
      {f.metrics.pricing === "unpriced" && <p>Saving unavailable: unpriced model.</p>}
      <dl className="finding-metrics">{Object.entries(f.metrics).map(([key, value]) => (
        <div key={key}><dt>{key.replace(/_/g, " ")}</dt>
          <dd>{value === null ? "Unavailable" : typeof value === "number"
            ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : value}</dd></div>
      ))}</dl>
      <div className="finding-actions">
        {f.session_id && <Link to={{ pathname: `/sessions/${encodeURIComponent(f.session_id)}`, search: location.search }}
          state={{ backgroundLocation: location }}>View session</Link>}
        <button onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
          {dismiss.isPending ? "Saving…" : f.dismissed ? "Restore finding" : "Dismiss finding"}
        </button>
      </div>
      {dismiss.isError && <p role="alert">Could not save dismissal: {dismiss.error.message}. Try again.</p>}
    </article>
  );
}
