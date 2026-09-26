import type { Summary } from "../api/client";



type Source = NonNullable<Summary["provenance"]>;

const SCOPES: Record<string, string> = { stored_local: "Local only", local: "Local only", harvested_session: "Harvested session", local_session: "Local session", account: "Account-wide", account_wide: "Account-wide", stored_cloud: "Harvested cloud only", harvested_cloud: "Harvested cloud only" };
const LABELS: Record<string, string> = {

  computed_local: "Computed from local logs", cloud_reported: "Cloud-reported",

  official: "Official", estimate: "Estimated API-equivalent",

};



export function Provenance({ summary, provenance }: { summary?: Summary; provenance?: Source | null }) {

  const source = provenance ?? summary?.provenance;

  if (!source) return null;

  return <p className="muted provenance">

    Usage: {LABELS[source.usage] ?? source.usage} · Cost: {LABELS[source.cost] ?? source.cost} · {SCOPES[source.scope] ?? source.scope.replaceAll("_", " ")} · {source.as_of ? <>Stored as of <time dateTime={source.as_of}>{source.as_of}</time></> : "Source timestamp unavailable"}

  </p>;

}

