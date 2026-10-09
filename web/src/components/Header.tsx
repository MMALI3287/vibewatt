import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useLocation, useNavigate } from "react-router";
import { getAccounts, postSync } from "../api/client";
import { useTheme } from "../lib/theme";
import { selectAccount, useAccountPreference } from "../lib/account";
import { DashboardExport } from "./DashboardExport";
import { BrowserNotifications } from "./BrowserNotifications";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/sessions", label: "Sessions" },
  { to: "/projects", label: "Projects" },
  { to: "/models", label: "Models" },
  { to: "/analysis", label: "Analysis" },
  { to: "/wrapped", label: "Wrapped" },
];

export function Header() {
  const { search } = useLocation();
  const navigate = useNavigate();
  const searchInput = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(new URLSearchParams(search).get("q") ?? "");
  useEffect(() => setQuery(new URLSearchParams(search).get("q") ?? ""), [search]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey ||
        (target instanceof HTMLElement && (target.isContentEditable || target.closest("input, textarea, select")))) return;
      event.preventDefault();
      searchInput.current?.focus();
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, []);
  const [theme, cycleTheme] = useTheme();
  const preference = useAccountPreference();
  const qc = useQueryClient();
  const sync = useMutation({ mutationFn: postSync, onSuccess: () => qc.invalidateQueries() });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: getAccounts });

  return (
    <header className="header">
      <Link className="logo" to={{ pathname: "/", search }}>
        vibewatt
      </Link>
      <nav className="nav" aria-label="Primary">
        {NAV.map((n) => (
          <NavLink key={n.to} to={{ pathname: n.to, search }} end={n.to === "/"}>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <form className="header-search" role="search" onSubmit={event => {
        event.preventDefault();
        const next = new URLSearchParams(search);
        if (query.trim()) next.set("q", query.trim()); else next.delete("q");
        navigate({ pathname: "/sessions", search: next.toString() });
      }}>
        <input ref={searchInput} type="search" aria-label="Search all sessions" placeholder="Search sessions (/)"
          maxLength={500} value={query} onChange={event => setQuery(event.target.value)} />
        <button type="submit">Search</button>
      </form>
      <div className="header-actions">
        <DashboardExport />
        <BrowserNotifications accountId={accounts.data?.selected} />
        {accounts.data && <select aria-label="Account" value={accounts.data.selected} style={{ maxWidth: "10rem" }}
          onChange={event => selectAccount(event.target.value)}>
          {accounts.data.accounts.map(account => <option key={account} value={account}>{account === "unknown" ? "Unknown account" : account}</option>)}
        </select>}
        {accounts.isError && <button type="button" onClick={() => {
          selectAccount(null);
          if (preference.account === null) void qc.invalidateQueries();
        }}>Reset account</button>}
        {!preference.persisted && <small role="status" className="muted">Storage unavailable: account selection lasts until reload.</small>}
        <button type="button" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? "Syncing…" : sync.isError ? "Sync failed" : "Sync"}
        </button>
        <button type="button" onClick={cycleTheme} aria-label={`Theme: ${theme}. Click to change.`}>
          Theme: {theme}
        </button>
      </div>
    </header>
  );
}
