import { describe, expect, it } from "vitest";
import { toQuery } from "./filters";

describe("d9 probe: toQuery", () => {
  it("forwards every filter to the API query", () => {
    const f = { from: "2026-08-01", to: "2026-08-31", source: "cowork", project: "p", model: "claude-opus-5", metric: "tokens" as const };
    expect(toQuery(f)).toEqual({ from: "2026-08-01", to: "2026-08-31", source: "cowork", project: "p", model: "claude-opus-5", metric: "tokens" });
  });
});
