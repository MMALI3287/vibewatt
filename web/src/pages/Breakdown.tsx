import { useQuery } from "@tanstack/react-query";
import { useTitle } from "../lib/title";
import { Link, useLocation } from "react-router";
import { getSummary } from "../api/client";
import { DataTable } from "../components/DataTable";
import { UsageSummary } from "../components/UsageSummary";
import { useFilters, serializeFilters } from "../lib/filters";
import { bucketTokens, fmtCompact, fmtInt, fmtUsd } from "../lib/format";
import { Sessions } from "./Sessions";

export function Breakdown({ dimension }: { dimension: "project" | "model" }) {
  const [filters] = useFilters();
  const location = useLocation();
  const query = useQuery({ queryKey: ["summary", filters], queryFn: () => getSummary(filters) });
  const title = dimension === "project" ? "Projects" : "Models";
  const rows = Object.entries((dimension === "project" ? query.data?.by_project : query.data?.by_model) ?? {})
    .map(([name, bucket]) => ({ name, cost: bucket.cost_usd, tokens: bucketTokens(bucket), responses: bucket.responses }))
    .sort((a, b) => filters.metric === "cost" ? b.cost - a.cost : b.tokens - a.tokens);
  const parentSearch = serializeFilters({ ...filters, [dimension]: null }).toString();
  return <>
    <h1>{title}</h1>
    <TitleFor view={title} />
    {filters[dimension] && <nav aria-label="Breadcrumbs" className="breadcrumbs">
      <Link to={{ pathname: location.pathname, search: parentSearch }}>{title}</Link><span>/</span><span>{filters[dimension]}</span>
    </nav>}
    <UsageSummary />
    <section className="card">
      <h2>{title} · local logs</h2>
      {query.isPending && <p role="status">Loading {title.toLowerCase()}…</p>}
      {query.isError && <p role="alert">Could not load {title.toLowerCase()}. <button onClick={() => query.refetch()}>Retry</button></p>}
      {query.isSuccess && <DataTable rows={rows} label={title} columns={[
        { accessorKey: "name", header: dimension === "project" ? "Project" : "Model", cell: info => {
          const name = info.row.original.name;
          return <Link to={{ pathname: location.pathname, search: serializeFilters({ ...filters, [dimension]: name }).toString() }}>{name}</Link>;
        } },
        { accessorKey: "cost", header: "Cost (priced responses)", cell: info => fmtUsd(info.row.original.cost) },
        { accessorKey: "tokens", header: "Tokens", cell: info => fmtCompact(info.row.original.tokens) },
        { accessorKey: "responses", header: "Responses", cell: info => fmtInt(info.row.original.responses) },
      ]} />}
    </section>
    {filters[dimension] && <Sessions embedded={dimension} />}
  </>;
}

function TitleFor({ view }: { view: string }) {
  useTitle(view);
  return null;
}
