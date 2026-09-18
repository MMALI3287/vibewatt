import { useQuery } from "@tanstack/react-query";
import { getQuota, type Summary } from "../api/client";
import { bucketTokens, fmtCompact, fmtPct, fmtUsd } from "../lib/format";
import type { Filters } from "../lib/filters";

export function Hero({ summary, filters }: { summary: Summary; filters: Filters }) {
  // Account-wide: filters do not change the answer, so they stay out of the cache key.
  const quota = useQuery({ queryKey: ["quota"], queryFn: () => getQuota(filters) });
  const headline =
    filters.metric === "tokens" ? `${fmtCompact(bucketTokens(summary.total))} tokens` : fmtUsd(summary.total.cost_usd);

  return (
    <section className="hero" aria-label="Headline">
      <div className="hero-main">
        <p className="hero-label">API-equivalent {filters.metric === "tokens" ? "volume" : "cost"} · local logs</p>
        <p className="hero-number" data-testid="hero-number">
          {headline}
        </p>
      </div>
      <div className="meters" aria-label="Plan utilization (account-wide)">
        {quota.isPending && <p className="muted">Loading plan utilization…</p>}
        {quota.isError && <p className="muted">Plan utilization unavailable</p>}
        {quota.isSuccess && !quota.data && <p className="muted">Plan utilization unavailable</p>}
        {quota.data?.windows.map((w) => {
          const pct = Math.min(1, Math.max(0, w.utilization / 100));
          return (
            <div className="meter" key={w.label}>
              <span className="meter-label">
                {w.label} <strong>{fmtPct(pct)}</strong>
              </span>
              <span className="meter-track" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={w.utilization} aria-label={w.label}>
                <span className="meter-fill" style={{ width: `${pct * 100}%` }} />
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
