import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { getActivity, getLocalContext } from "../api/client";
import { fmtInt } from "../lib/format";

export function LocalActivity() {
  const activity = useQuery({ queryKey: ["activity"], queryFn: getActivity });
  const context = useQuery({ queryKey: ["context"], queryFn: getLocalContext });
  const data = activity.data;
  const dates = Object.keys(data?.days ?? {}).sort();
  const end = dates.length ? Date.parse(`${dates[dates.length - 1]}T00:00:00Z`) : 0;
  const calendar = dates.length ? Array.from({ length: 91 }, (_, i) => {
    const day = new Date(end - (90 - i) * 86400000).toISOString().slice(0, 10);
    return { day, count: data?.days[day] ?? 0 };
  }) : [];
  return <>
    {!!context.data?.length && <section className="card" aria-label="Local context nudges">
      <h2>Local context · active sessions</h2>
      <p className="muted">Latest main-thread response within 30 minutes. Capacity uses a conservative 200K window unless the session shows 1M evidence. This is a local snapshot, not plan utilization.</p>
      <ul>{context.data.map(nudge => <li key={`${nudge.source}:${nudge.session}`}>
        <Link to={`/sessions/${encodeURIComponent(nudge.session)}`}>
          {nudge.severity === "urgent" ? "Near capacity" : "Consider a handoff"}: {fmtInt(nudge.used_tokens)} / {fmtInt(nudge.max_tokens)} tokens
        </Link>
      </li>)}</ul>
    </section>}
    <section className="card" aria-label="Activity calendar">
      <h2>Activity calendar · local history</h2>
      <p className="muted">All local history activity in the report timezone, independent of usage filters. These entries add no tokens or cost and contain no prompt text.</p>
      {activity.isPending && <p>Loading activity…</p>}
      {activity.isError && <p role="status">Activity unavailable</p>}
      {data && <>
        <p>Current streak: {data.current_streak} days · Longest streak: {data.longest_streak} days</p>
        {data.truncated && <p className="notice">History input was truncated at the 50 MB / 500,000 record sync limit.</p>}
        {!dates.length ? <p>No local history activity recorded.</p> : <>
          <p className="muted">91 days ending {dates[dates.length - 1]}. Active days are highlighted.</p>
          <ol className="activity-calendar" aria-label="Activity days">{calendar.map(({ day, count }) =>
            <li key={day} className={count ? "active" : ""} title={`${day}: ${count} entries`} aria-label={`${day}: ${count} activity entries`}>
              <time dateTime={day}>{day.slice(5)}</time><span>{count}</span>
            </li>)}</ol>
        </>}
      </>}
    </section>
  </>;
}

