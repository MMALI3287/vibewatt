import { test, expect } from "@playwright/test";

test("selecting an account reloads the dashboard with isolated requests", async ({ page }) => {
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
});
