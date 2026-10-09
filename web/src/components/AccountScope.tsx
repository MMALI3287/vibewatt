import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAccountPreference } from "../lib/account";

function AccountQueries({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
  }));
  useEffect(() => () => { void client.cancelQueries(); client.clear(); }, [client]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export function AccountScope({ children }: { children: ReactNode }) {
  const { account } = useAccountPreference();
  // A new account gets fresh query and component state, including pending requests.
  return <AccountQueries key={account ?? ""}>{children}</AccountQueries>;
}
