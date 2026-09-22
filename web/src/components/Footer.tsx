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
        Token and cost figures cover local logs plus harvested cloud sessions only. Plan
        utilization is the one account-wide number.
      </span>
      <span>vibewatt v{__APP_VERSION__}</span>
    </footer>
  );
}
