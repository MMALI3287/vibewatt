import { useEffect, useRef } from "react";
import { useTitle } from "../lib/title";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getSession, getSessions, SESSION_PAGE_SIZE, type SessionDetail } from "../api/client";
import { useFilters } from "../lib/filters";
import { fmtCompact, fmtPct, fmtUsd } from "../lib/format";
import { UsageSummary } from "../components/UsageSummary";
import { DataTable } from "../components/DataTable";

export function duration(start?: string | null, end?: string | null) {
  if (!start || !end) return "Unavailable";
  const minutes = Math.max(0, Math.round((Date.parse(end) - Date.parse(start)) / 60000));
  return Number.isFinite(minutes) ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : "Unavailable";
}

export function Sessions({ embedded }: { embedded?: "project" | "model" }) {
  const [filters] = useFilters();
  useTitle(embedded ? (embedded === "model" ? "Models" : "Projects") : "Sessions");
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const q = params.get("q") ?? "";
  const sentinel = useRef<HTMLDivElement>(null);
  const query = useInfiniteQuery({
    queryKey: ["sessions", filters, q], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => getSessions(filters, q, pageParam),
    getNextPageParam: last => last.length === SESSION_PAGE_SIZE ? last.at(-1)?.cursor ?? undefined : undefined,
  });
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && query.hasNextPage && !query.isFetching && !query.isError) void query.fetchNextPage();
    });
    if (sentinel.current) observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [query.hasNextPage, query.isFetching, query.isError, query.fetchNextPage]);
  const rows = query.data?.pages.flat() ?? [];
  return <>
    {!embedded && <><h1>Sessions</h1><UsageSummary /></>}
    <section className="card" aria-label="Sessions">
      <h2>{embedded === "model" ? "Model sessions" : embedded === "project" ? "Project sessions" : "Local and harvested sessions"}</h2>
      <p className="muted">Local totals follow the filters. Cloud totals are selected by start date and retain the API cost. Detail shows the full session.</p>
      <label className="search-label">Search titles, projects or models
        <input type="search" aria-label="Search sessions" maxLength={500} value={q} onChange={event => {
          const next = new URLSearchParams(params);
          if (event.target.value) next.set("q", event.target.value); else next.delete("q");
          setParams(next, { replace: true });
        }} />
      </label>
      {query.isPending && <p role="status">Loading sessions…</p>}
      {query.isError && <p role="alert">Could not load sessions. <button onClick={() => query.isFetchNextPageError ? query.fetchNextPage() : query.refetch()}>Retry</button></p>}
      {query.isSuccess && !rows.length && <p>No sessions match. Widen the filters or use Sync to import local logs.</p>}
      {rows.length > 0 && <div className="table-wrap"><table aria-label="Session list">
        <thead><tr><th>Title</th><th>Surface</th><th>Project</th><th>Models</th><th>Started</th><th>Duration</th><th>Tokens</th><th>Cost</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}>
          <td><Link to={{ pathname: `/sessions/${encodeURIComponent(row.id)}`, search: location.search }}
            state={{ backgroundLocation: location }}>{row.title}</Link></td>
          <td>{row.surface ?? "Unknown"}{row.harvested ? " · harvested" : ""}</td>
          <td>{row.project ?? "Unknown"}</td><td>{row.model ?? "Unknown"}</td>
          <td>{row.started ?? "Unavailable"}</td><td>{duration(row.started, row.ended)}</td>
          <td>{fmtCompact(row.tokens)}</td><td>{fmtUsd(row.cost)}{row.unpriced_turns ? " + unpriced" : ""}</td>
        </tr>)}</tbody>
      </table></div>}
      <div ref={sentinel} className="pager">
        {query.hasNextPage && <button disabled={query.isFetching} onClick={() => query.fetchNextPage()}>
          {query.isFetchingNextPage ? "Loading…" : "Load more sessions"}
        </button>}
      </div>
    </section>
  </>;
}

export function SessionModal({ hasBackground }: { hasBackground: boolean }) {
  const { id = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const query = useQuery({ queryKey: ["session", id], queryFn: () => getSession(id) });
  const close = () => hasBackground ? navigate(-1) : navigate(`/sessions${location.search}`, { replace: true });
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    return () => { element?.close(); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  return <dialog ref={dialog} className="session-dialog" aria-labelledby="session-heading" onCancel={event => { event.preventDefault(); close(); }}>
    <button className="close-modal" onClick={close} autoFocus>Close session</button>
    <h2 id="session-heading">{query.data?.title ?? "Session detail"}</h2>
    {query.isPending && <p role="status">Loading session…</p>}
    {query.isError && <p role="alert">Could not load session: {query.error.message} <button onClick={() => query.refetch()}>Retry</button></p>}
    {query.data && <SessionContents session={query.data} />}
  </dialog>;
}

function SessionContents({ session: s }: { session: SessionDetail }) {
  const location = useLocation();
  const projectParams = new URLSearchParams(location.search);
  projectParams.delete("q");
  const allProjectsParams = new URLSearchParams(projectParams);
  allProjectsParams.delete("project");
  if (s.project && s.project !== "-") projectParams.set("project", s.project);
  return <>
    <nav aria-label="Breadcrumbs" className="breadcrumbs">
      <Link to={`/projects?${allProjectsParams}`}>Projects</Link><span>/</span>
      {s.project && s.project !== "-" && <><Link to={`/projects?${projectParams}`}>{s.project}</Link><span>/</span></>}
      <span>{s.title}</span>
    </nav>
    <p>{s.harvested ? "Harvested API totals. Per-response detail is unavailable." : "Full local session. Totals here are not restricted by the list filters."}</p>
    <dl className="session-facts">
      <dt>Surface</dt><dd>{s.surface ?? "Unknown"}</dd>
      <dt>Models</dt><dd>{s.model ?? "Unknown"}</dd>
      <dt>Started</dt><dd>{s.started ?? "Unavailable"}</dd>
      <dt>Duration</dt><dd>{duration(s.started, s.ended)}</dd>
      <dt>Tokens</dt><dd>{fmtCompact(s.tokens)}</dd>
      <dt>Cost</dt><dd>{fmtUsd(s.cost)}{s.unpriced_turns ? ` + ${s.unpriced_turns} unpriced responses` : ""}</dd>
      <dt>Context usage (latest harvest)</dt><dd>{s.context_used != null && s.context_max ? `${fmtPct(s.context_used / s.context_max)} (${fmtCompact(s.context_used)} / ${fmtCompact(s.context_max)})` : "Unavailable"}</dd>
    </dl>
    {!s.harvested && <DataTable rows={s.turns} label="Session turns" columns={[
      { accessorKey: "ts", header: "Time" }, { accessorKey: "model", header: "Model" },
      { accessorKey: "input", header: "Input" }, { accessorKey: "cache_5m", header: "Cache write · 5m" },
      { accessorKey: "cache_1h", header: "Cache write · 1h" }, { accessorKey: "cache_read", header: "Cache read" },
      { accessorKey: "output", header: "Output" },
      { accessorKey: "cost", header: "Cost", cell: info => info.row.original.cost == null ? "Unpriced" : fmtUsd(info.row.original.cost) },
    ]} />}
  </>;
}
