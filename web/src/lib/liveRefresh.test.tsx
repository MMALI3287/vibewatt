import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canLiveRefresh, useLiveRefresh } from "./liveRefresh";
import { datePreset } from "../components/FilterBar";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.restoreAllMocks());

describe("report date presets", () => {
  it("uses the supplied report day across a year boundary and leap day", () => {
    expect(datePreset("week", "2026-01-03")).toEqual({ from: "2025-12-28", to: "2026-01-03" });
    expect(datePreset("week", "2024-03-03")).toEqual({ from: "2024-02-26", to: "2024-03-03" });
    expect(datePreset("month", "2024-02-29")).toEqual({ from: "2024-02-01", to: "2024-02-29" });
    expect(datePreset("all", "2026-01-03")).toEqual({ from: null, to: null });
  });
});

describe("bounded live refresh", () => {
  it("excludes external calls, running requests and multi-page lists", () => {
    const client = new QueryClient();
    for (const key of ["quota", "summary", "activity", "context", "service-status", "concierge", "weekly-summary", "sessions"]) {
      client.setQueryData([key], key === "sessions" ? { pages: [[], []] } : {});
    }
    const eligible = client.getQueryCache().getAll().filter(canLiveRefresh).map(query => query.queryKey[0]);
    expect(eligible).toEqual(["quota", "summary", "activity", "context"]);
    const summary = client.getQueryCache().find({ queryKey: ["summary"] })!;
    summary.setState({ fetchStatus: "fetching" });
    expect(canLiveRefresh(summary)).toBe(false);
    client.clear();
  });
  it("only ticks while visible and online and stops after unmount", async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const element = document.createElement("div");
    const root = createRoot(element);
    function Live() { useLiveRefresh(); return null; }
    await act(async () => root.render(<QueryClientProvider client={client}><Live /></QueryClientProvider>));
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(invalidate).toHaveBeenCalledTimes(1);
    visible.mockReturnValue("hidden");
    await act(async () => vi.advanceTimersByTime(180_000));
    expect(invalidate).toHaveBeenCalledTimes(1);
    visible.mockReturnValue("visible"); online.mockReturnValue(false);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(invalidate).toHaveBeenCalledTimes(1);
    online.mockReturnValue(true);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(invalidate).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(invalidate).toHaveBeenCalledTimes(2);
    vi.useRealTimers(); client.clear();
  });
});
