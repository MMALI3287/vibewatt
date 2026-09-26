import { Provenance } from "./Provenance";
import { useQuery } from "@tanstack/react-query";
import { getSummary } from "../api/client";
import { Kpis } from "../pages/Overview";
import { useFilters } from "../lib/filters";

export function UsageSummary() {
  const [filters] = useFilters();
  const query = useQuery({ queryKey: ["summary", filters], queryFn: () => getSummary(filters) });
  return <section aria-label="Filtered local usage">
    <p className="muted">Local logs only. Session search does not change these figures. Harvested cloud totals appear in Sessions.</p>
    {query.isPending ? <p role="status">Loading usage…</p> : query.isError ?
      <p role="alert">Could not load usage. <button onClick={() => query.refetch()}>Retry</button></p> : <>
        {query.data.unknown_models.length > 0 && <p className="notice">Unpriced models excluded from cost: {query.data.unknown_models.join(", ")}</p>}
        <Provenance summary={query.data} />
        <Kpis s={query.data} />
      </>}
  </section>;
}
