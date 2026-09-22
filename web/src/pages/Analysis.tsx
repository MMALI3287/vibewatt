import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { dismissFinding, getFinding, getFindings, type Finding } from "../api/client";
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
const SEVERITIES = ["all", "urgent", "warning", "info"] as const;
type SeverityChoice = (typeof SEVERITIES)[number];

export function Analysis() {
  const [filters] = useFilters();
  const [params, setParams] = useSearchParams();
  // Both controls live in the URL, so a reload or a shared link keeps them (A-123).
  const severity: SeverityChoice = (SEVERITIES as readonly string[]).includes(params.get("severity") ?? "")
    ? (params.get("severity") as SeverityChoice)
    : "all";
  const includeDismissed = params.get("dismissed") === "1";
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
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
        <label>Severity <select aria-label="Severity" value={severity}
          onChange={e => setParam("severity", e.target.value === "all" ? null : e.target.value)}>
          <option value="all">All severities</option><option value="urgent">Urgent</option>
          <option value="warning">Warning</option><option value="info">Info</option>
        </select></label>
        <label><input type="checkbox" checked={includeDismissed}
          onChange={e => setParam("dismissed", e.target.checked ? "1" : null)} /> Show dismissed</label>
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
        // "Could not look" must not read as "nothing found" (A-093).
        const cannot = kind === "anomaly" ? query.data.anomaly_notes ?? [] : [];
        return (
          <details className="card analysis-group" key={kind}>
            <summary>{label} <span className="finding-count">{group.length}</span></summary>
            {cannot.map(note => <p key={note} className="notice">{note}</p>)}
            {group.length === 0 ? <p>No matching findings.</p> :
              group.map(f => <FindingCard key={f.id} finding={f} />)}
          </details>
        );
      })}
    </section>
  );
}

function useDismiss(f: Finding) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => dismissFinding(f.id, !f.dismissed),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["analysis"] });
      qc.invalidateQueries({ queryKey: ["finding", f.id] });
    },
  });
}

function FindingCard({ finding: f }: { finding: Finding }) {
  const location = useLocation();
  const dismiss = useDismiss(f);
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
      <div className="finding-actions">
        <Link to={{ pathname: `/analysis/findings/${encodeURIComponent(f.id)}`, search: location.search }}
          state={{ backgroundLocation: location }}>Details</Link>
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

/** The route-backed finding detail from the section 6 spec (A-046). */
export function FindingModal({ hasBackground }: { hasBackground: boolean }) {
  const { id = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const query = useQuery({ queryKey: ["finding", id], queryFn: () => getFinding(id) });
  const close = () => hasBackground ? navigate(-1) : navigate(`/analysis${location.search}`, { replace: true });
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    return () => { element?.close(); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  return <dialog ref={dialog} className="session-dialog" aria-labelledby="finding-heading"
    onCancel={event => { event.preventDefault(); close(); }}>
    <button className="close-modal" onClick={close} autoFocus>Close finding</button>
    <h2 id="finding-heading">{query.data?.title ?? "Finding"}</h2>
    {query.isPending && <p role="status">Loading finding…</p>}
    {query.isError && <p role="alert">Could not load finding: {query.error.message}. It may be from an older analysis. <button onClick={() => query.refetch()}>Retry</button></p>}
    {query.data && <FindingDetail finding={query.data} />}
  </dialog>;
}

function FindingDetail({ finding: f }: { finding: Finding }) {
  const location = useLocation();
  const dismiss = useDismiss(f);
  return <>
    <p className="finding-meta">{f.kind} · {f.rule} · {f.severity}{f.day ? ` · ${f.day}` : ""}{f.dismissed ? " · dismissed" : ""}</p>
    <p>{f.detail}</p>
    {f.savings_usd !== null && <p className="finding-saving">Estimated potential saving: {fmtUsd(f.savings_usd)}</p>}
    {f.metrics.pricing === "unpriced" && <p>Saving unavailable: unpriced model.</p>}
    <dl className="finding-metrics">{Object.entries(f.metrics).map(([key, value]) => (
      <div key={key}><dt>{key.replace(/_/g, " ")}</dt>
        <dd>{value === null ? "Unavailable" : typeof value === "number"
          ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : value}</dd></div>
    ))}</dl>
    <div className="finding-actions">
      {f.session_id && <Link to={{ pathname: `/sessions/${encodeURIComponent(f.session_id)}`, search: location.search }}>View session</Link>}
      <button onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
        {f.dismissed ? "Restore finding" : "Dismiss finding"}
      </button>
    </div>
  </>;
}
