import { test, expect } from "@playwright/test";

test("selecting an account persists across reloads with isolated requests", async ({ page }) => {
  const other = "22222222-2222-4222-8222-222222222222";
  await page.route("**/api/accounts", route => route.fulfill({ json: {
    selected: route.request().headers()["x-vibewatt-account"] || "unknown",
    accounts: ["unknown", other],
  } }));
  await page.goto("/");
  const selected = page.getByRole("combobox", { name: "Account" });
  await expect(selected).toHaveValue("unknown");
  await selected.selectOption(other);
  await expect(selected).toHaveValue(other);
  expect(await page.evaluate(() => localStorage.getItem("vibewatt-account"))).toBe(other);
  await page.reload();
  await expect(selected).toHaveValue(other);
});

test("blocked storage still permits rendering and switching without stale account data", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      Storage.prototype[method] = () => { throw new DOMException("Storage blocked", "SecurityError"); };
    }
  });
  const other = "22222222-2222-4222-8222-222222222222";
  await page.route("**/api/accounts", route => route.fulfill({ json: {
    selected: route.request().headers()["x-vibewatt-account"] || "unknown",
    accounts: ["unknown", other],
  } }));
  await page.route("**/api/sessions?**", route => route.request().headers()["x-vibewatt-account"] === other
    ? route.fulfill({ json: [] }) : route.continue());
  await page.goto("/sessions?project=demo");
  await expect(page.getByRole("link", { name: "fix the sync" })).toBeVisible();
  const selected = page.getByRole("combobox", { name: "Account" });
  await expect(selected).toHaveValue("unknown");
  await selected.selectOption(other);
  await expect(selected).toHaveValue(other);
  await expect(page.getByText("No sessions match.", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "fix the sync" })).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "account selection lasts until reload" })).toBeVisible();
  await selected.selectOption("unknown");
  await expect(page.getByRole("link", { name: "fix the sync" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("reset recovers from an invalid account even if removing the saved preference fails", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem("vibewatt-account", "../invalid-account");
    Storage.prototype.removeItem = () => { throw new DOMException("Storage blocked", "SecurityError"); };
  });
  await page.goto("/sessions?project=demo");
  await page.getByRole("button", { name: "Reset account" }).click();
  await expect(page.getByRole("combobox", { name: "Account" })).toHaveValue("unknown");
  await expect(page.getByRole("link", { name: "fix the sync" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "account selection lasts until reload" })).toBeVisible();
  expect(errors).toEqual([]);
});
