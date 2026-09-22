import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/client";

export function Footer() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const turns = health.data?.turns as { lo?: string | null; hi?: string | null } | undefined;

  return (
    <footer className="footer">
      <span>
        {health.isPending && "Checking data freshness…"}
        {health.isError && "Store status unavailable"}
        {health.data &&
          (turns?.hi ? `Local logs ${turns.lo} to ${turns.hi}` : "No local logs synced yet")}
        {health.data?.last_harvest && ` · last cloud harvest ${health.data.last_harvest}`}
      </span>
      <span>
        Overview, Projects and Models count local logs. Sessions and Wrapped add harvested
        cloud sessions. Plan utilization is the one account-wide number.
      </span>
      <nav aria-label="About vibewatt" className="footer-links">
        <a href="https://github.com/MMALI3287/vibewatt">Source</a>
        <a href="https://github.com/MMALI3287/vibewatt/blob/master/docs/DATA-SOURCES.md">Data sources</a>
        <a href="https://github.com/MMALI3287/vibewatt/issues">Report a problem</a>
        <span>vibewatt v{__APP_VERSION__}</span>
      </nav>
    </footer>
  );
}
