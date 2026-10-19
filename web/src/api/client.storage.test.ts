import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => { vi.resetModules(); localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("account preference when storage is unavailable", () => {
  it("imports the API client when getItem throws SecurityError", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage blocked", "SecurityError");
    });
    await expect(import("./client")).resolves.toHaveProperty("api");
  });

  it("imports the API client when accessing localStorage itself throws", async () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("Storage blocked", "SecurityError");
    });
    await expect(import("./client")).resolves.toHaveProperty("api");
  });

  it("omits the account header when the saved preference cannot be read", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const requests: Request[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      requests.push(request);
      return new Response("{}", { headers: { "Content-Type": "application/json" } });
    });
    const { api } = await import("./client");
    await api.GET("/api/accounts", { baseUrl: "http://localhost" });
    expect(requests[0].headers.has("X-Vibewatt-Account")).toBe(false);
  });

  it("uses the in-memory selection and reset when persistence fails", async () => {
    localStorage.setItem("vibewatt-account", "saved-account");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
    const requests: Request[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      requests.push(request);
      return new Response("{}", { headers: { "Content-Type": "application/json" } });
    });
    const { api } = await import("./client");
    const { selectAccount, getAccountPreference } = await import("../lib/account");
    await api.GET("/api/accounts", { baseUrl: "http://localhost" });
    selectAccount("another-account");
    await api.GET("/api/accounts", { baseUrl: "http://localhost" });
    selectAccount(null);
    await api.GET("/api/accounts", { baseUrl: "http://localhost" });
    expect(requests.map(request => request.headers.get("X-Vibewatt-Account"))).toEqual([
      "saved-account", "another-account", null,
    ]);
    expect(getAccountPreference()).toEqual({ account: null, persisted: false });
    expect(localStorage.getItem("vibewatt-account")).toBe("saved-account");
  });

  it("persists selection and removes it on reset when storage works", async () => {
    const { selectAccount, getAccountPreference } = await import("../lib/account");
    selectAccount("saved-account");
    expect(localStorage.getItem("vibewatt-account")).toBe("saved-account");
    expect(getAccountPreference().persisted).toBe(true);
    selectAccount(null);
    expect(localStorage.getItem("vibewatt-account")).toBeNull();
    expect(getAccountPreference()).toEqual({ account: null, persisted: true });
  });
});
