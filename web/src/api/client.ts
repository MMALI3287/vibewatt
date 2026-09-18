import createClient from "openapi-fetch";
import type { components, paths } from "./schema";
import { toQuery, type Filters } from "../lib/filters";

export type Summary = components["schemas"]["SummaryOut"];
export type Bucket = components["schemas"]["BucketOut"];
export type Quota = components["schemas"]["QuotaOut"];
export type Health = components["schemas"]["HealthOut"];

export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {}

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.error !== undefined || res.data === undefined) {
    const detail =
      typeof res.error === "object" && res.error !== null && "detail" in res.error
        ? String((res.error as { detail: unknown }).detail)
        : res.response.statusText;
    throw new ApiError(`${res.response.status} ${detail}`);
  }
  return res.data;
}

export async function getSummary(f: Filters): Promise<Summary> {
  return unwrap(await api.GET("/api/summary", { params: { query: toQuery(f) } }));
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
