import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type Metric = "cost" | "tokens";

export interface Filters {
  from: string | null;
  to: string | null;
  source: string;
  project: string | null;
  model: string | null;
  metric: Metric;
}

export const DEFAULT_FILTERS: Filters = {
  from: null,
  to: null,
  source: "all",
  project: null,
  model: null,
  metric: "cost",
};

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function parseFilters(params: URLSearchParams): Filters {
  const day = (k: string) => {
    const v = params.get(k);
    return v && ISO_DAY.test(v) ? v : null;
  };
  const text = (k: string) => params.get(k) || null;
  return {
    from: day("from"),
    to: day("to"),
    source: text("source") ?? "all",
    project: text("project"),
    model: text("model"),
    metric: params.get("metric") === "tokens" ? "tokens" : "cost",
  };
}

// Defaults are omitted so the canonical URL of an unfiltered view is bare.
export function serializeFilters(f: Filters): URLSearchParams {
  const out = new URLSearchParams();
  if (f.from) out.set("from", f.from);
  if (f.to) out.set("to", f.to);
  if (f.source !== "all") out.set("source", f.source);
  if (f.project) out.set("project", f.project);
  if (f.model) out.set("model", f.model);
  if (f.metric !== "cost") out.set("metric", f.metric);
  return out;
}

export function toQuery(f: Filters) {
  return {
    from: f.from ?? undefined,
    to: f.to ?? undefined,
    source: f.source,
    project: f.project ?? undefined,
    model: f.model ?? undefined,
    metric: f.metric,
  };
}

export function useFilters(): [Filters, (patch: Partial<Filters>) => void] {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const update = useCallback(
    (patch: Partial<Filters>) => {
      const next = serializeFilters({ ...filters, ...patch });
      if (params.get("q")) next.set("q", params.get("q")!);
      setParams(next);
    },
    [filters, params, setParams],
  );
  return [filters, update];
}
