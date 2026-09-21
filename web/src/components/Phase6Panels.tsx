import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { generateWeeklySummary, getAlerts, getConcierge, getServiceStatus } from "../api/client";
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
  const query = useQuery({ queryKey: ["alerts"], queryFn: getAlerts, staleTime: 60_000 });
  return <details className="card phase6-panel">
    <summary>Burn and spike alerts{query.data ? ` (${query.data.alerts.length})` : ""}</summary>
    <p>Quota trends are account-wide. Response spikes and burn rates cover stored local usage. Page filters do not apply.</p>
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
    <p>Off by default. When enabled in your ccburn configuration, generating sends aggregate usage to Claude. Prompt text is excluded and project names require a separate opt-in.</p>
    <button disabled={result.isPending} onClick={() => result.mutate()}>{result.isPending ? "Generating…" : "Generate weekly summary"}</button>
    {result.isError && <p role="alert">Could not generate summary: {result.error.message}</p>}
    {result.data && <div role="status"><p>{result.data.detail}</p>{result.data.text && <p className="phase6-copy">{result.data.text}</p>}</div>}
  </details>;
}

function Concierge() {
  const [filters] = useFilters();
  const project = filters.project;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const result = useQuery({ queryKey: ["concierge", project], queryFn: () => getConcierge(project!), enabled: false });
  async function copy() {
    if (!result.data) return;
    try { await navigator.clipboard.writeText(result.data.text); setCopied(true); setCopyError(false); }
    catch { setCopyError(true); }
  }
  return <details className="card phase6-panel">
    <summary>Project resume brief</summary>
    <p>Copy a brief with the latest session, working-tree changes and unfinished todos. Nothing is sent to a running session.</p>
    {!project ? <p>Select a project in the filter bar to prepare its brief.</p> : <>
      <button disabled={result.isFetching} onClick={() => { setCopied(false); void result.refetch(); }}>{result.isFetching ? "Preparing…" : `Prepare brief for ${project}`}</button>
      {result.isError && <p role="alert">Could not prepare brief: {result.error.message}</p>}
      {result.data && <><textarea aria-label="Project resume brief" readOnly value={result.data.text} />
        <button onClick={() => void copy()}>{copied ? "Copied" : "Copy brief"}</button>
        {copyError && <p role="status">Select the brief text to copy it.</p>}
        {result.data.notes.map(note => <p key={note}>{note}</p>)}
      </>}
    </>}
  </details>;
}
