import { describe, expect, it } from "vitest";
import { NOTIFICATION_COOLDOWN, emptyNotificationState, selectNotifications, deliverNotifications, failedNotification } from "./notifications";

const alert = (id: string, kind = "burn", severity: "info" | "warning" | "serious" = "warning") => ({
  id, kind, severity, title: "Usage warning", detail: "Account utilization", created_at: "2026-10-01T00:00:00Z",
});

describe("browser notification receipts", () => {
  it("defaults off and excludes informational alerts", () => {
    expect(emptyNotificationState().enabled).toBe(false);
    expect(selectNotifications([alert("i", "burn", "info"), alert("s", "spike", "serious")], emptyNotificationState(), 100)).toEqual([alert("s", "spike", "serious")]);
  });
  it("dedups ids permanently and limits each kind to one delivery per six hours", () => {
    const state = { ...emptyNotificationState(), seen: ["old"], kinds: { burn: 100 } };
    expect(selectNotifications([alert("old", "spike"), alert("new"), alert("a", "spike"), alert("b", "spike")], state, 100 + NOTIFICATION_COOLDOWN - 1)).toEqual([alert("a", "spike")]);
    expect(selectNotifications([alert("old"), alert("new")], state, 100 + NOTIFICATION_COOLDOWN)).toEqual([alert("new")]);
  });
  it("records only successful delivery so failure cannot consume dedup or cooldown", () => {
    const initial = emptyNotificationState();
    const result = deliverNotifications([alert("failed"), alert("ok", "spike")], initial, 100, a => a.id !== "failed");
    expect(result.seen).toEqual(["ok"]);
    expect(result.kinds).toEqual({ spike: 100 });
    expect(initial.seen).toEqual([]);
    expect(selectNotifications([alert("failed")], result, 101)).toEqual([alert("failed")]);
  });
  it("keeps accounts isolated through separate receipts", () => {
    const first = deliverNotifications([alert("shared")], emptyNotificationState(), 100, () => true);
    expect(selectNotifications([alert("shared")], first, 101)).toEqual([]);
    expect(selectNotifications([alert("shared")], emptyNotificationState(), 101)).toHaveLength(1);
  });
  it("an asynchronous browser error removes only its receipt and restores the prior cooldown", () => {
    const initial = { ...emptyNotificationState(), enabled: true, kinds: { burn: 10 } };
    const accepted = deliverNotifications([alert("failed"), alert("ok", "spike")], initial, 100 + NOTIFICATION_COOLDOWN, () => true);
    const restored = failedNotification(accepted, alert("failed"), 100 + NOTIFICATION_COOLDOWN, 10);
    expect(restored).toEqual({ enabled: false, seen: ["ok"], kinds: { burn: 10, spike: 100 + NOTIFICATION_COOLDOWN } });
  });
});
