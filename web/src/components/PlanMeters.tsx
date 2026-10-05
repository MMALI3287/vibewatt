import { useQuery } from "@tanstack/react-query";
import { getCodexQuota, getQuota, type Quota } from "../api/client";
import { DEFAULT_FILTERS } from "../lib/filters";
import { fmtPct } from "../lib/format";

const clamp = (v: number) => Math.min(100, Math.max(0, v));

function Windows({ quota }: { quota: Quota }) {
  return (
    <>
      {quota.windows.map((w) => {
        const pct = clamp(w.utilization);
        const pace = w.pace_delta;
        return (
          <div className="meter" key={`${w.key}:${w.scope}`}>
            <span className="meter-label">
              {w.label} <strong>{fmtPct(pct / 100)}</strong>
              {pace != null && (
                <span className="muted">
                  {" "}
                  · {pace >= 0 ? "+" : ""}
                  {pace.toFixed(0)} pts vs even pace
                </span>
              )}
            </span>
            <span
              className="meter-track"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
              aria-label={w.label}
            >
              <span className="meter-fill" style={{ width: `${pct}%` }} />
            </span>
            {w.band ? (
              <span className="meter-note">
                At reset: {w.band.p10.toFixed(0)}–{w.band.p90.toFixed(0)}% (median {w.band.p50.toFixed(0)}%)
              </span>
            ) : (
              w.note && <span className="meter-note muted">{w.note}</span>
            )}
          </div>
        );
      })}
    </>
  );
}

/**
 * Account-wide plan utilization. It does not depend on local usage or on
 * filters, so it renders even when the local summary is empty, loading or
 * failed (A-036). A Codex plan is a separate ChatGPT limit, shown in its own
 * group only when local rollouts carried a reading.
 */
export function PlanMeters() {
  const quota = useQuery({ queryKey: ["quota"], queryFn: () => getQuota(DEFAULT_FILTERS) });
  const codex = useQuery({ queryKey: ["codex-quota"], queryFn: getCodexQuota });

  return (
    <>
      <div className="meters" aria-label="Plan utilization (account-wide)">
        {quota.isPending && <p className="muted">Loading plan utilization…</p>}
        {quota.isError && <p className="muted">Plan utilization unavailable</p>}
        {/* No reading, or a reading with no windows, is the same to a reader (A-100). */}
        {quota.isSuccess && !quota.data?.windows.length && <p className="muted">Plan utilization unavailable</p>}
        {quota.data && <p className="meter-note muted">Claude plan, official account-wide utilization · source: {quota.data.source} · fetched <time dateTime={quota.data.fetched_at}>{quota.data.fetched_at}</time>. Forecasts are estimates.</p>}
        {quota.data && <Windows quota={quota.data} />}
      </div>
      {codex.data && codex.data.windows.length > 0 && (
        <div className="meters" aria-label="Codex plan utilization (ChatGPT account)">
          <p className="meter-note muted">Codex plan, ChatGPT account · read from local Codex logs at <time dateTime={codex.data.fetched_at}>{codex.data.fetched_at}</time>.</p>
          <Windows quota={codex.data} />
        </div>
      )}
    </>
  );
}
