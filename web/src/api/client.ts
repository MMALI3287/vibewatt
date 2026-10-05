import createClient from "openapi-fetch";
import type { components, paths } from "./schema";
import { toQuery, type Filters } from "../lib/filters";

export type Summary = components["schemas"]["SummaryOut"];
export type Bucket = components["schemas"]["BucketOut"];
export type Quota = components["schemas"]["QuotaOut"];
export type Health = components["schemas"]["HealthOut"];

export const api = createClient<paths>({ baseUrl: "" });
const selectedAccount = localStorage.getItem("vibewatt-account");
api.use({ onRequest({ request }) {
  if (selectedAccount) request.headers.set("X-Vibewatt-Account", selectedAccount);
  return request;
} });

export async function getAccounts() {
  return unwrap(await api.GET("/api/accounts"));
}

export class ApiError extends Error {}

/**
 * A readable message from an API error body. FastAPI's 422 detail is a list of
 * {loc, msg}; String() of it rendered "[object Object]" (A-080).
 */
export function errorMessage(status: number, body: unknown, statusText: string): string {
  const detail = typeof body === "object" && body !== null && "detail" in body
    ? (body as { detail: unknown }).detail : undefined;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const { loc, msg } = item as { loc?: unknown; msg?: unknown };
      const where = Array.isArray(loc) ? loc.filter((p) => p !== "query" && p !== "body").join(".") : "";
      return where ? `${where}: ${String(msg)}` : String(msg);
    });
    return `${status} ${parts.join("; ")}`;
  }
  if (typeof detail === "string") return `${status} ${detail}`;
  return `${status} ${statusText}`.trim();
}

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.error !== undefined || res.data === undefined) {
    throw new ApiError(errorMessage(res.response.status, res.error, res.response.statusText));
  }
  return res.data;
}

export async function getSummary(f: Filters): Promise<Summary> {
  return unwrap(await api.GET("/api/summary", { params: { query: toQuery(f) } }));
}

export async function getCodexQuota(): Promise<Quota | null> {
  const res = await api.GET("/api/codex-quota");
  if (!res.response.ok) throw new ApiError(`${res.response.status} ${res.response.statusText}`);
  return res.data ?? null;
}

export async function getQuota(f: Filters): Promise<Quota | null> {
  const res = await api.GET("/api/quota", { params: { query: toQuery(f) } });
  // A null body is a valid "no quota source" answer, so unwrap's missing-data check does not apply.
  if (res.error !== undefined) throw new ApiError(`${res.response.status} ${res.response.statusText}`);
  return res.data ?? null;
}

export async function getHealth(): Promise<Health> {
  return unwrap(await api.GET("/api/health"));
}

export async function getActivity() {
  return unwrap(await api.GET("/api/activity"));
}

export async function getLocalContext() {
  return unwrap(await api.GET("/api/context"));
}

export async function postSync() {
  return unwrap(await api.POST("/api/sync"));
}

export type Session = components["schemas"]["SessionOut"];
export type SessionDetail = components["schemas"]["SessionDetailOut"];
export type Block = components["schemas"]["BlockOut"];

export const SESSION_PAGE_SIZE = 40;

export async function getSessions(f: Filters, q: string, cursor?: string) {
  return unwrap(await api.GET("/api/sessions", {
    params: { query: { ...toQuery(f), q: q || undefined, cursor, limit: SESSION_PAGE_SIZE } },
  }));
}

export async function getSession(id: string) {
  return unwrap(await api.GET("/api/sessions/{session_id}", { params: { path: { session_id: id } } }));
}

export async function getBlocks(f: Filters) {
  return unwrap(await api.GET("/api/blocks", { params: { query: toQuery(f) } }));
}

export async function getSessionFacets() {
  return unwrap(await api.GET("/api/session-facets"));
}

export type Finding = components["schemas"]["FindingOut"];

export async function getFindings(f: Filters, includeDismissed: boolean) {
  return unwrap(await api.GET("/api/findings", {
    params: { query: { ...toQuery(f), include_dismissed: includeDismissed } },
  }));
}

export async function getFinding(id: string) {
  return unwrap(await api.GET("/api/findings/{finding_id}", { params: { path: { finding_id: id } } }));
}

export async function getReconciliation(f: Filters) {
  return unwrap(await api.GET("/api/reconciliation", {
    params: { query: { from: f.from ?? undefined, to: f.to ?? undefined } },
  }));
}

export async function dismissFinding(id: string, dismissed: boolean) {
  return unwrap(await api.POST("/api/findings/{finding_id}/dismiss", {
    params: { path: { finding_id: id } }, body: { dismissed },
  }));
}

export type WrappedReport = components["schemas"]["WrappedOut"];

export async function getWrapped(year: number | undefined, f: Filters) {
  return unwrap(await api.GET("/api/wrapped", {
    params: { query: { year, source: f.source, project: f.project ?? undefined, model: f.model ?? undefined } },
  }));
}

export async function getAlerts() {
  return unwrap(await api.GET("/api/alerts"));
}

export async function getServiceStatus() {
  const res = await api.GET("/api/status");
  if (!res.response.ok) throw new ApiError(`${res.response.status} ${res.response.statusText}`);
  return res.data ?? null;
}

export async function generateWeeklySummary() {
  return unwrap(await api.POST("/api/weekly-summary"));
}

export async function getConcierge(project: string) {
  return unwrap(await api.GET("/api/concierge", { params: { query: { project } } }));
}

export async function getReportContext() {
  return unwrap(await api.GET("/api/report-context"));
}
