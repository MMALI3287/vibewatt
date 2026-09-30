import { useState } from "react";
import { getSessionFacets } from "../api/client";
import { exportDashboardPng } from "../lib/exportView";
import "./DashboardExport.css";

export function DashboardExport() {
  const [includeProjectNames, setIncludeProjectNames] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function download() {
    setBusy(true);
    setError("");
    try {
      const app = document.querySelector<HTMLElement>(".app");
      if (!app) throw new Error("The dashboard is not ready to export.");
      let projects: string[] | undefined;
      if (!includeProjectNames) {
        try { projects = (await getSessionFacets()).projects; }
        catch { throw new Error("Project names could not be loaded. Retry before exporting with names masked."); }
      }
      const blob = await exportDashboardPng(app, { projects, includeProjectNames });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `vibewatt-${window.location.pathname.split("/").filter(Boolean)[0] || "overview"}.png`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "PNG export failed. Please try again.");
    } finally { setBusy(false); }
  }

  return <div className="dashboard-export-controls" data-export-exclude>
    <label><input type="checkbox" checked={includeProjectNames} disabled={busy}
      onChange={event => setIncludeProjectNames(event.target.checked)} />Include project names</label>
    <button type="button" disabled={busy} onClick={() => void download()}>{busy ? "Exporting…" : "Export PNG"}</button>
    {error && <span role="alert">{error}</span>}
  </div>;
}
