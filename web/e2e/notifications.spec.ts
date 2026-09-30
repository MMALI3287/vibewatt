import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/status", route => route.fulfill({ status: 503, body: "unavailable" }));
  await page.addInitScript(() => {
    class NotificationFixture {
      static permission: NotificationPermission = "granted";
      static async requestPermission() {
        localStorage.setItem("fixture-permission-requests", String(Number(localStorage.getItem("fixture-permission-requests") ?? 0) + 1));
        return NotificationFixture.permission;
      }
      constructor(title: string) {
        const titles: string[] = JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]");
        localStorage.setItem("fixture-notifications", JSON.stringify([...titles, title]));
      }
    }
    Object.defineProperty(window, "Notification", { value: NotificationFixture, configurable: true });
  });
});

test("explicit opt-in persists receipts and suppresses repeated ids and kinds across reloads", async ({ page }) => {
  let id = "first";
  await page.route("**/api/alerts", route => route.fulfill({ json: {
    alerts: [
      { id: "info", kind: "information", severity: "info", title: "Information", detail: "Info only", created_at: "now" },
      { id, kind: "burn", severity: "warning", title: "Quota warning", detail: "Fixture alert", created_at: "now" },
    ], new_count: 1, active_block: null, notes: [],
  } }));
  await page.clock.install();
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Browser notifications: off" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("fixture-permission-requests"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("fixture-notifications"))).toBeNull();
  await page.getByRole("button", { name: "Browser notifications: off" }).click();
  await expect(page.getByRole("button", { name: "Browser notifications: on" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]"))).toEqual(["vibewatt: Quota warning"]);
  await page.reload();
  await expect(page.getByRole("button", { name: "Browser notifications: on" })).toBeVisible();
  await page.clock.runFor(60_010);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]"))).toHaveLength(1);
  id = "second";
  await page.reload();
  await expect(page.getByRole("button", { name: "Browser notifications: on" })).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]"))).toHaveLength(1);
  await page.clock.fastForward(6 * 60 * 60 * 1000);
  await page.reload();
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]").length)).toBe(2);
  await page.getByRole("button", { name: "Browser notifications: on" }).click();
  id = "third";
  await page.clock.fastForward(6 * 60 * 60 * 1000);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("fixture-notifications") ?? "[]"))).toHaveLength(2);
});

test("denied permission and unsupported delivery stay reviewable and off", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => Object.defineProperty(Notification, "permission", { value: "denied" }));
  await page.getByRole("button", { name: "Browser notifications: off" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Notifications blocked" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Browser notifications: off" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("fixture-notifications"))).toBeNull();
});
