import type { Summary } from "../api/client";
import { bucketTokens, fmtCompact, fmtUsd } from "../lib/format";
import type { Filters } from "../lib/filters";

export function Hero({ summary, filters }: { summary: Summary; filters: Filters }) {
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
    </section>
  );
}
