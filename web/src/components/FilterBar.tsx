import { useQuery } from "@tanstack/react-query";
import { getReportContext, getSessionFacets, getSummary } from "../api/client";
import { DEFAULT_FILTERS, toSource, useFilters, type Metric } from "../lib/filters";

// Options come from the unfiltered summary so picking one value never hides the others.
export function FilterBar() {
  const [filters, update] = useFilters();
  const context = useQuery({ queryKey: ["report-context"], queryFn: getReportContext });
  const options = useQuery({
    queryKey: ["summary", DEFAULT_FILTERS],
    queryFn: () => getSummary(DEFAULT_FILTERS),
  });
  const keys = (o?: Record<string, unknown>) => Object.keys(o ?? {}).sort();
  const facets = useQuery({ queryKey: ["session-facets"], queryFn: getSessionFacets });
  const merge = (local: string[], stored?: string[]) => [...new Set([...local, ...(stored ?? [])])].sort();
  const sources = merge(keys(options.data?.by_source), facets.data?.sources);
  const projects = merge(keys(options.data?.by_project), facets.data?.projects);
  const models = merge(keys(options.data?.by_model), facets.data?.models);

  return (
    <form className="filters" aria-label="Filters" onSubmit={(e) => e.preventDefault()}>
      <label>
        Date preset
        <select aria-label="Date preset" value="" disabled={!context.data} onChange={event => {
          if (context.data) update(datePreset(event.target.value, context.data.today));
        }}>
          <option value="">Choose range</option>
          <option value="today">Today</option>
          <option value="week">Last 7 days</option>
          <option value="month">This month</option>
          <option value="all">All time</option>
        </select>
      </label>
      <label>
        From
        <input
          type="date"
          name="from"
          value={filters.from ?? ""}
          onChange={(e) => update({ from: e.target.value || null })}
        />
      </label>
      <label>
        To
        <input
          type="date"
          name="to"
          value={filters.to ?? ""}
          onChange={(e) => update({ to: e.target.value || null })}
        />
      </label>
      <label>
        Surface
        <select name="source" value={filters.source} onChange={(e) => update({ source: toSource(e.target.value) })}>
          <option value="all">All providers</option>
          <option value="claude">All Claude surfaces</option>
          {withCurrent(sources, filters.source === "all" || filters.source === "claude" ? null : filters.source).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Project
        <select
          name="project"
          value={filters.project ?? ""}
          onChange={(e) => update({ project: e.target.value || null })}
        >
          <option value="">All</option>
          {withCurrent(projects, filters.project).map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <label>
        Model
        <select
          name="model"
          value={filters.model ?? ""}
          onChange={(e) => update({ model: e.target.value || null })}
        >
          <option value="">All</option>
          {withCurrent(models, filters.model).map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>
      <label>
        Metric
        <select
          name="metric"
          value={filters.metric}
          onChange={(e) => update({ metric: e.target.value as Metric })}
        >
          <option value="cost">Cost</option>
          <option value="tokens">Tokens</option>
        </select>
      </label>
      <button type="button" onClick={() => update(DEFAULT_FILTERS)}>
        Reset
      </button>
      {context.data && <span className="muted">Report days: {context.data.timezone} · start {context.data.day_start_hour}:00</span>}
      {context.isError && <span role="status">Date presets unavailable</span>}
      {(facets.isError || options.isError) && <span role="alert">
        Some filter choices could not load; the current selection still applies.{" "}
        <button type="button" onClick={() => { void facets.refetch(); void options.refetch(); }}>Retry choices</button>
      </span>}
    </form>
  );
}

// A value from a shared URL must stay selectable before the options load, or the select shows "All".
function withCurrent(list: string[], current: string | null): string[] {
  return current && !list.includes(current) ? [current, ...list] : list;
}

export function datePreset(preset: string, today: string): { from: string | null; to: string | null } {
  if (preset === "all") return { from: null, to: null };
  const day = new Date(`${today}T12:00:00Z`);
  if (preset === "week") day.setUTCDate(day.getUTCDate() - 6);
  if (preset === "month") day.setUTCDate(1);
  return { from: day.toISOString().slice(0, 10), to: today };
}
