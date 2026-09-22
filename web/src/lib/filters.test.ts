import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, parseFilters, serializeFilters } from "./filters";

describe("filters <-> URL", () => {
  it("round-trips a fully specified filter", () => {
    const f = {
      from: "2026-08-01",
      to: "2026-08-31",
      source: "claude-code" as const,
      project: "vibewatt",
      model: "claude-opus-5",
      metric: "tokens" as const,
    };
    expect(parseFilters(serializeFilters(f))).toEqual(f);
  });

  it("serializes defaults to an empty query", () => {
    expect(serializeFilters(DEFAULT_FILTERS).toString()).toBe("");
  });

  it("drops malformed dates and unknown metrics instead of sending them to the API", () => {
    const f = parseFilters(new URLSearchParams("from=yesterday&metric=vibes"));
    expect(f.from).toBeNull();
    expect(f.metric).toBe("cost");
  });
});

describe("source filter", () => {
  it("falls back to all for a value the API would reject", () => {
    expect(parseFilters(new URLSearchParams("source=bogus")).source).toBe("all");
    expect(parseFilters(new URLSearchParams("source=cowork")).source).toBe("cowork");
  });
});
