import { useQuery } from "@tanstack/react-query";
import { getSessionFacets, getSummary } from "../api/client";
import { DEFAULT_FILTERS, toSource, useFilters, type Metric } from "../lib/filters";

// Options come from the unfiltered summary so picking one value never hides the others.
export function FilterBar() {
  const [filters, update] = useFilters();
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
          <option value="all">All</option>
          {withCurrent(sources, filters.source === "all" ? null : filters.source).map((s) => (
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
      {facets.isError && <span role="alert">Stored filter choices unavailable. <button type="button" onClick={() => facets.refetch()}>Retry choices</button></span>}
    </form>
  );
}

// A value from a shared URL must stay selectable before the options load, or the select shows "All".
function withCurrent(list: string[], current: string | null): string[] {
  return current && !list.includes(current) ? [current, ...list] : list;
}
