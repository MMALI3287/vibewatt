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
