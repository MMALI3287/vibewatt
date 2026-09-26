import { useEffect } from "react";
import { useQueryClient, type Query } from "@tanstack/react-query";

const STORE_QUERIES = new Set(["quota", "summary", "health", "report-context", "session-facets", "sessions", "session", "blocks", "analysis", "finding", "reconciliation", "wrapped", "alerts"]);

export function canLiveRefresh(query: Query): boolean {
  if (!STORE_QUERIES.has(String(query.queryKey[0])) || query.state.fetchStatus !== "idle") return false;
  const data = query.state.data;
  // Infinite lists can grow without a bound. Keep the reader's loaded pages until an explicit reload.
  return !(query.queryKey[0] === "sessions" && typeof data === "object" && data !== null &&
    "pages" in data && Array.isArray(data.pages) && data.pages.length > 1);
}

export function useLiveRefresh() {
  const client = useQueryClient();
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible" || !navigator.onLine) return;
      void client.invalidateQueries({ predicate: canLiveRefresh, refetchType: "active" }, { cancelRefetch: false });
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [client]);
}
