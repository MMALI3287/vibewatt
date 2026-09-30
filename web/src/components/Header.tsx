import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useLocation, useNavigate } from "react-router";
import { getAccounts, postSync } from "../api/client";
import { useTheme } from "../lib/theme";

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
        {accounts.data && <select aria-label="Account" value={accounts.data.selected} style={{ maxWidth: "10rem" }}
          onChange={event => { localStorage.setItem("vibewatt-account", event.target.value); window.location.reload(); }}>
          {accounts.data.accounts.map(account => <option key={account} value={account}>{account === "unknown" ? "Unknown account" : account}</option>)}
        </select>}
        {accounts.isError && <button type="button" onClick={() => { localStorage.removeItem("vibewatt-account"); window.location.reload(); }}>Reset account</button>}
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
