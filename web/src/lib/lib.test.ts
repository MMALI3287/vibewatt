import { describe, expect, it } from "vitest";
import { errorMessage } from "../api/client";
import { barLayout } from "./chart";
import { isCalendarDay, parseFilters } from "./filters";
import { bucketTokens, fmtCompact, fmtPct, fmtUsd } from "./format";

describe("format", () => {
  it("keeps a sub-cent cost distinguishable from zero (A-079)", () => {
    expect(fmtUsd(0.004)).toBe("<$0.01");
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtUsd(0.01)).toBe("$0.01");
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
  });
  it("formats compact numbers and percentages", () => {
    expect(fmtCompact(15_000_000)).toBe("15M");
    expect(fmtPct(0.235)).toBe("24%");
  });
  it("counts tokens like the backend's total_tokens", () => {
    expect(bucketTokens({ responses: 1, input: 1, cache_write_5m: 2, cache_write_1h: 3, cache_read: 4,
      output: 5, thinking: 99, web_searches: 0, cost_usd: 0, unpriced: 0 })).toBe(15);
  });
});

describe("dates", () => {
  it("accepts only real calendar days (A-082)", () => {
    expect(isCalendarDay("2026-02-28")).toBe(true);
    expect(isCalendarDay("2028-02-29")).toBe(true);
    expect(isCalendarDay("2026-02-29")).toBe(false);
    expect(isCalendarDay("2026-13-45")).toBe(false);
    expect(isCalendarDay("0001-01-01")).toBe(false);
    expect(isCalendarDay("26-1-1")).toBe(false);
  });
  it("drops an impossible date from the URL instead of failing the view", () => {
    const f = parseFilters(new URLSearchParams("from=2026-13-45&to=2026-09-30"));
    expect(f.from).toBeNull();
    expect(f.to).toBe("2026-09-30");
  });
});

describe("error client", () => {
  it("renders FastAPI validation errors readably (A-080)", () => {
    const body = { detail: [{ loc: ["query", "from"], msg: "must be between 1970-01-01 and 9998-12-31" },
      { loc: ["query", "metric"], msg: "Input should be 'cost' or 'tokens'" }] };
    expect(errorMessage(422, body, "Unprocessable Entity")).toBe(
      "422 from: must be between 1970-01-01 and 9998-12-31; metric: Input should be 'cost' or 'tokens'");
  });
  it("uses a string detail or the status text", () => {
    expect(errorMessage(409, { detail: "a sync is already running" }, "Conflict")).toBe("409 a sync is already running");
    expect(errorMessage(500, undefined, "Internal Server Error")).toBe("500 Internal Server Error");
  });
});

describe("daily chart", () => {
  it("keeps the newest day inside the chart at any range (A-037)", () => {
    for (const count of [1, 30, 240, 241, 1000, 3650]) {
      const { barWidth, gap } = barLayout(count, 720);
      const lastRight = (count - 1) * (barWidth + gap) + barWidth;
      expect(lastRight).toBeLessThanOrEqual(720 + 1e-9);
      expect(barWidth).toBeGreaterThan(0);
    }
  });
});
