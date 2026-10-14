import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { generateWeeklySummary, getHealth, getAlerts, getConcierge, getServiceStatus } from "../api/client";
import { useFilters } from "../lib/filters";
import { fmtCompact, fmtUsd } from "../lib/format";

export function Phase6Panels() {
  return <aside className="phase6-panels" aria-label="Usage tools"><ServiceStatus /><Alerts /><WeeklySummary /><Concierge /></aside>;
}

function ServiceStatus() {
  const query = useQuery({ queryKey: ["service-status"], queryFn: getServiceStatus, staleTime: 300_000, retry: false });
  if (!query.data || query.isError || query.isPending) return null;
  return <section className="card phase6-status" aria-label="Anthropic service status">
    <h2>Anthropic service status</h2><p>{query.data.description}</p>
    <a href={query.data.url} target="_blank" rel="noreferrer">View service status</a>
  </section>;
}

function Alerts() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const query = useQuery({ queryKey: ["alerts"], queryFn: getAlerts, staleTime: 60_000 });
  return <details className="card phase6-panel">
    <summary>Burn and spike alerts{query.data ? ` (${query.data.alerts.length})` : ""}</summary>
    <p>Quota trends are account-wide. Response spikes and burn rates cover stored local usage. Page filters do not apply. Forecasts and burn projections are estimates.</p>
    <p className="muted">Local store synced: {health.data?.last_sync ?? "unavailable"}. Cloud harvested: {health.data?.last_harvest ?? "unavailable"}. Quota readings retain their source timestamps in Plan utilization.</p>
    {query.isPending && <p role="status">Loading alerts…</p>}
    {query.isError && <p role="alert">Could not load alerts. <button onClick={() => query.refetch()}>Retry alerts</button></p>}
    {query.data && <>
      {query.data.alerts.length === 0 && <p>No alerts detected.</p>}
      {query.data.alerts.map(alert => <article className="finding" key={alert.id}><h3>{alert.title}</h3><p>{alert.detail}</p></article>)}
      {query.data.active_block && <p>Active local block: {fmtCompact(query.data.active_block.tokens_per_minute)} tokens/min · {fmtUsd(query.data.active_block.cost_per_minute)}/min.</p>}
      {query.data.notes.map(note => <p key={note}>{note}</p>)}
    </>}
  </details>;
}

function WeeklySummary() {
  const result = useMutation({ mutationFn: generateWeeklySummary });
  return <details className="card phase6-panel">
    <summary>AI weekly summary</summary>
    <p>Off by default. When enabled in your vibewatt configuration, generating sends aggregate usage to Claude. Prompt text is excluded and project names require a separate opt-in.</p>
    <button disabled={result.isPending} onClick={() => result.mutate()}>{result.isPending ? "Generating…" : "Generate weekly summary"}</button>
    {result.isError && <p role="alert">Could not generate summary: {result.error.message}</p>}
    {result.data && <div role="status"><p>{result.data.detail}</p>{result.data.text && <p className="phase6-copy">{result.data.text}</p>}</div>}
  </details>;
}

function Concierge() {
  const [filters] = useFilters();
  const project = filters.project;
  const [preparation, setPreparation] = useState(0);
  const result = useQuery({ queryKey: ["concierge", project], queryFn: () => getConcierge(project!), enabled: false });
  return <details className="card phase6-panel">
    <summary>Project resume brief</summary>
    <p>Copy a brief with the latest session, working-tree changes and unfinished todos. Nothing is sent to a running session.</p>
    {!project ? <p>Select a project in the filter bar to prepare its brief.</p> : <>
      <button disabled={result.isFetching} onClick={() => { setPreparation(value => value + 1); void result.refetch(); }}>{result.isFetching ? "Preparing…" : `Prepare brief for ${project}`}</button>
      {result.isError && <p role="alert">Could not prepare brief: {result.error.message}</p>}
      {result.data && !result.isFetching && !result.isError && <BriefCopy
        key={JSON.stringify([project, preparation, result.data.text])}
        text={result.data.text} notes={result.data.notes}
      />}
    </>}
  </details>;
}

// Remount for a new project, preparation or text so late clipboard results stay
// with the brief that initiated them, even when returning to cached text.
function BriefCopy({ text, notes }: { text: string; notes: string[] }) {
  const [feedback, setFeedback] = useState<"idle" | "copied" | "error">("idle");
  async function copy() {
    try { await navigator.clipboard.writeText(text); setFeedback("copied"); }
    catch { setFeedback("error"); }
  }
  return <><textarea aria-label="Project resume brief" readOnly value={text} />
    <button onClick={() => void copy()}>{feedback === "copied" ? "Copied" : "Copy brief"}</button>
    {feedback === "error" && <p role="status">Select the brief text to copy it.</p>}
    {notes.map(note => <p key={note}>{note}</p>)}
  </>;
}
