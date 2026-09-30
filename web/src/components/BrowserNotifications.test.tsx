import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserNotifications } from "./BrowserNotifications";
import { getAlerts } from "../api/client";
import { emptyNotificationState, notificationStorageKey, readNotificationState, saveNotificationState } from "../lib/notifications";

vi.mock("../api/client", () => ({ getAlerts: vi.fn() }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const warning = { id: "one", kind: "burn", severity: "warning" as const, title: "Warning", detail: "Details", created_at: "now" };
let root: Root;
let element: HTMLDivElement;
let client: QueryClient;
let deliveries: string[];
let requestPermission: ReturnType<typeof vi.fn>;
let permission: NotificationPermission;

beforeEach(() => {
  localStorage.clear();
  permission = "granted";
  deliveries = [];
  requestPermission = vi.fn(async () => permission);
  class BrowserNotification {
    static get permission() { return permission; }
    static requestPermission = requestPermission;
    constructor(title: string) { deliveries.push(title); }
  }
  vi.stubGlobal("Notification", BrowserNotification);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  vi.mocked(getAlerts).mockReset().mockResolvedValue({ alerts: [warning], new_count: 1, notes: [], active_block: null });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  element = document.createElement("div");
  document.body.append(element);
  root = createRoot(element);
});
afterEach(async () => {
  await act(async () => root.unmount());
  element.remove(); client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function mount() {
  await act(async () => root.render(<QueryClientProvider client={client}><BrowserNotifications accountId="account-a" /></QueryClientProvider>));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
async function click() {
  await act(async () => element.querySelector("button")!.click());
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}

describe("browser notification opt-in", () => {
  it("does not request permission, fetch alerts or deliver until an explicit click", async () => {
    await mount();
    expect(requestPermission).not.toHaveBeenCalled();
    expect(getAlerts).not.toHaveBeenCalled();
    expect(deliveries).toEqual([]);
    await click();
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(deliveries).toEqual(["vibewatt: Warning"]);
    expect(readNotificationState("account-a").seen).toEqual(["one"]);
    await click();
    expect(readNotificationState("account-a").enabled).toBe(false);
  });
  it("a denied permission never enables or delivers", async () => {
    permission = "denied";
    await mount(); await click();
    expect(getAlerts).not.toHaveBeenCalled();
    expect(deliveries).toEqual([]);
    expect(element.textContent).toContain("Notifications blocked");
  });
  it("hidden or offline tabs neither fetch nor deliver", async () => {
    saveNotificationState("account-a", { ...emptyNotificationState(), enabled: true });
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await mount();
    expect(getAlerts).not.toHaveBeenCalled();
    await act(async () => {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
      vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(getAlerts).not.toHaveBeenCalled(); expect(deliveries).toEqual([]);
  });
  it("storage failure leaves notifications off without a toast", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    await mount(); await click();
    expect(deliveries).toEqual([]);
    expect(element.textContent).toContain("storage unavailable");
  });
  it("failed delivery disables notifications and keeps the failed id retryable", async () => {
    class FailingNotification {
      static permission = "granted";
      static requestPermission = requestPermission;
      constructor() { throw new Error("delivery unsupported"); }
    }
    vi.stubGlobal("Notification", FailingNotification);
    await mount(); await click();
    expect(readNotificationState("account-a")).toEqual(emptyNotificationState());
    expect(element.textContent).toContain("delivery unavailable");
  });
  it("a browser error after construction removes its receipt and disables delivery", async () => {
    let notification: { onerror: ((event: Event) => void) | null } | undefined;
    class DeferredErrorNotification {
      static permission = "granted";
      static requestPermission = requestPermission;
      onerror: ((event: Event) => void) | null = null;
      constructor() { notification = this; }
    }
    vi.stubGlobal("Notification", DeferredErrorNotification);
    await mount(); await click();
    expect(readNotificationState("account-a").seen).toEqual(["one"]);
    await act(async () => notification!.onerror!(new Event("error")));
    expect(readNotificationState("account-a")).toEqual(emptyNotificationState());
    expect(element.textContent).toContain("delivery unavailable");
  });
  it("unsupported browsers show an unavailable disabled control", async () => {
    vi.stubGlobal("Notification", undefined);
    await mount();
    expect(element.querySelector("button")!.disabled).toBe(true);
    expect(localStorage.getItem(notificationStorageKey("account-a"))).toBeNull();
  });
});
