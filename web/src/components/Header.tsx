import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useLocation } from "react-router";
import { postSync } from "../api/client";
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
  const [theme, cycleTheme] = useTheme();
  const qc = useQueryClient();
  const sync = useMutation({ mutationFn: postSync, onSuccess: () => qc.invalidateQueries() });

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
      <div className="header-actions">
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
