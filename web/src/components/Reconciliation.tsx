import { useQuery } from "@tanstack/react-query";
import { getReconciliation } from "../api/client";
import type { Filters } from "../lib/filters";
import { fmtCompact, fmtInt } from "../lib/format";

/**
 * vibewatt's deduped figures beside what Claude's own Stats would show for the
 * same days. The Stats figure is a comparison, never a headline (A-116, A-125).
 */
export function Reconciliation({ filters }: { filters: Filters }) {
  const query = useQuery({
    queryKey: ["reconciliation", filters.from, filters.to],
    queryFn: () => getReconciliation(filters),
  });
  return (
    <details className="card reconciliation">
      <summary>Why these numbers differ from Claude&apos;s Stats</summary>
      {query.isPending && <p role="status">Loading comparison…</p>}
      {query.isError && <p role="alert">Comparison unavailable: {query.error.message}</p>}
      {query.data && (
        <>
          <p className="muted">Claude Code only, for the selected dates. Project and model filters do not apply.</p>
          <div className="table-wrap">
            <table aria-label="vibewatt compared with Claude's Stats">
              <thead>
                <tr>
                  <th scope="col">Figure</th>
                  <th scope="col">vibewatt (deduped)</th>
                  <th scope="col">Claude Stats-equivalent</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Input + output tokens</th>
                  <td>{fmtCompact(query.data.deduped.input_output_tokens)}</td>
                  <td>{fmtCompact(query.data.stats_equivalent.tokens)}</td>
                </tr>
                <tr>
                  <th scope="row">Responses / messages</th>
                  <td>{fmtInt(query.data.deduped.responses)} responses</td>
                  <td>{fmtInt(query.data.stats_equivalent.messages)} messages</td>
                </tr>
                <tr>
                  <th scope="row">Sessions</th>
                  <td>{fmtInt(query.data.deduped.sessions)}</td>
                  <td>{fmtInt(query.data.stats_equivalent.sessions)}</td>
                </tr>
                <tr>
                  <th scope="row">All tokens, cache included</th>
                  <td>{fmtCompact(query.data.deduped.all_tokens)}</td>
                  <td>Not counted</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ul>
            {query.data.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <p>
            <strong>Session:</strong> {query.data.session_definition}
          </p>
        </>
      )}
    </details>
  );
}
