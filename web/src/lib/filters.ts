import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type Metric = "cost" | "tokens";
export type Source = "all" | "claude-code" | "cowork" | "web";
const SOURCES: readonly Source[] = ["all", "claude-code", "cowork", "web"];
const isSource = (v: string | null): v is Source => v !== null && (SOURCES as readonly string[]).includes(v);

/** A select or URL value as a Source; anything the API would reject means all. */
export const toSource = (v: string | null): Source => (isSource(v) ? v : "all");

export interface Filters {
  from: string | null;
  to: string | null;
  source: Source;
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

/** A real calendar day: 2026-13-45 matches the pattern but is dropped (A-082). */
export function isCalendarDay(v: string | null): v is string {
  if (!v || !ISO_DAY.test(v)) return false;
  const [y, m, d] = v.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
    && y >= 1970 && y <= 9998;
}

export function parseFilters(params: URLSearchParams): Filters {
  const day = (k: string) => {
    const v = params.get(k);
    return isCalendarDay(v) ? v : null;
  };
  const text = (k: string) => params.get(k) || null;
  return {
    from: day("from"),
    to: day("to"),
    // An unknown source in a hand-edited URL falls back to all, not a 422.
    source: toSource(params.get("source")),
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
      if (params.get("year")) next.set("year", params.get("year")!);
      setParams(next);
    },
    [filters, params, setParams],
  );
  return [filters, update];
}
