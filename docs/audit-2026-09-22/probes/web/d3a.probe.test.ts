import { describe, expect, it } from "vitest";
import { bucketTokens, fmtCompact, fmtPct, fmtUsd } from "./format";
import { parseFilters, toQuery } from "./filters";
import { duration } from "../pages/Sessions";

// d3a probe: pins current behaviour of untested frontend helpers.
describe("d3a probe: format", () => {
  it("fmtUsd hides sub-cent costs", () => {
    expect(fmtUsd(0.000675)).toBe("$0.00");
    expect(fmtUsd(0.004)).toBe("$0.00");
    expect(fmtUsd(0.005)).toBe("$0.01");
  });
  it("fmtPct rounds 99.6% cache hit to 100% and 0.4% to 0%", () => {
    expect(fmtPct(0.996)).toBe("100%");
    expect(fmtPct(0.004)).toBe("0%");
  });
  it("fmtCompact and bucketTokens", () => {
    expect(fmtCompact(1_234_567)).toBe("1.2M");
    expect(bucketTokens({ responses: 1, input: 1, cache_write_5m: 2, cache_write_1h: 3, cache_read: 4, output: 5, thinking: 5, web_searches: 0, cost_usd: 0 })).toBe(15);
  });
});

describe("d3a probe: filters and duration", () => {
  it("parseFilters accepts a shape-valid impossible date", () => {
    expect(parseFilters(new URLSearchParams("from=2026-13-45")).from).toBe("2026-13-45");
  });
  it("toQuery always sends source and metric", () => {
    expect(toQuery(parseFilters(new URLSearchParams("")))).toMatchObject({ source: "all", metric: "cost" });
  });
  it("duration formats and degrades", () => {
    expect(duration("2026-09-16T00:00:00Z", "2026-09-16T01:05:00Z")).toBe("1h 5m");
    expect(duration("2026-09-16T01:00:00Z", "2026-09-16T00:00:00Z")).toBe("0h 0m");
    expect(duration("garbage", "2026-09-16T00:00:00Z")).toBe("Unavailable");
    expect(duration(null, "2026-09-16T00:00:00Z")).toBe("Unavailable");
  });
});
