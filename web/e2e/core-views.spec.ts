import { expect, test } from "@playwright/test";

test("core filters recompute KPIs and survive reload", async ({ page }) => {
  await page.goto("/projects");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  await page.getByRole("table", { name: "Projects", exact: true }).getByRole("link", { name: "demo", exact: true }).click();
  await expect(page.getByTestId("kpi-responses")).toHaveText("2");
  await expect(page.getByRole("navigation", { name: "Breadcrumbs" })).toContainText("demo");
  await expect(page.getByRole("link", { name: "fix the sync" })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("kpi-responses")).toHaveText("2");
  await page.locator('input[name="from"]').fill("2030-01-01");
  await expect(page.getByTestId("kpi-responses")).toHaveText("0");
  await expect(page.getByText("No sessions match.", { exact: false })).toBeVisible();
});

test("session modal deep links and Back restores project list", async ({ page }) => {
  await page.goto("/projects?project=demo");
  const link = page.getByRole("link", { name: "fix the sync" });
  await link.click();
  await expect(page).toHaveURL(/\/sessions\/s1\?project=demo/);
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("table", { name: "Session turns" })).toContainText("claude-opus-5");
  await page.goBack();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/projects\?project=demo/);
  await expect(link).toBeFocused();
  await page.goto("/sessions/s1?project=demo");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close session" }).click();
  await expect(page).toHaveURL(/\/sessions\?project=demo/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("infinite scrolling preserves timestamp ties and search includes later pages", async ({ page }) => {
  await page.goto("/sessions");
  const table = page.getByRole("table", { name: "Session list" });
  await expect(table.locator("tbody tr")).toHaveCount(40);
  await page.getByRole("button", { name: "Load more sessions" }).scrollIntoViewIfNeeded();
  await expect(table.locator("tbody tr")).toHaveCount(47);
  await page.getByRole("searchbox", { name: "Search sessions" }).fill("fix the sync");
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(table).toContainText("fix the sync");
  await page.locator('select[name="source"]').selectOption("claude-code");
  await expect(page.getByRole("searchbox", { name: "Search sessions" })).toHaveValue("fix the sync");
  await page.reload();
  await expect(table.locator("tbody tr")).toHaveCount(1);
});

test("harvest detail retains API cost and reports context without invented turns", async ({ page }) => {
  await page.goto("/sessions/cloud-00");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("$1.23");
  await expect(dialog).toContainText("45%");
  await expect(dialog).toContainText("Per-response detail is unavailable");
  await expect(dialog.getByRole("table")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("cloud-only surface project and model are selectable", async ({ page }) => {
  await page.goto("/sessions");
  await page.locator('select[name="source"]').selectOption("web");
  await page.locator('select[name="project"]').selectOption("cloud-project");
  await page.locator('select[name="model"]').selectOption("cloud-only-model");
  const table = page.getByRole("table", { name: "Session list" });
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(table).toContainText("Cloud session 00");
  await expect(table).toContainText("$1.23");
  await expect(page.getByTestId("kpi-responses")).toHaveText("0");
});

test("heatmap metric switches and models drill down", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Daily cost heatmap" })).toBeVisible();
  await page.locator('select[name="metric"]').selectOption("tokens");
  await expect(page.getByRole("region", { name: "Daily tokens heatmap" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Hour-of-day usage" })).toContainText("Hour-of-day tokens");
  await page.getByRole("link", { name: "Models", exact: true }).click();
  await page.getByRole("table", { name: "Models", exact: true }).getByRole("link", { name: "claude-opus-5" }).click();
  await expect(page).toHaveURL(/model=claude-opus-5/);
  await expect(page.getByRole("table", { name: "Session list" })).toBeVisible();
});

for (const width of [1440, 1024, 768, 390]) {
  test(`core views have no page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ["/", "/sessions", "/projects?project=demo", "/models", "/sessions/s1"]) {
      await page.goto(path);
      await expect(page.getByTestId("kpi-responses")).toBeVisible();
      if (path === "/sessions/s1") await expect(page.getByRole("dialog")).toContainText("fix the sync");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    }
  });
}

test("sessions handles empty results and API errors", async ({ page }) => {
  await page.goto("/sessions?q=nonexistent-title");
  await expect(page.getByText("No sessions match.", { exact: false })).toBeVisible();
  await page.route("**/api/sessions?**", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await page.goto("/sessions");
  await expect(page.getByRole("alert").filter({ hasText: "Could not load sessions" })).toBeVisible();
  await page.unroute("**/api/sessions?**");
  await page.getByRole("alert").getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("table", { name: "Session list" })).toBeVisible();
  await page.goto("/sessions/missing");
  await expect(page.getByRole("dialog")).toContainText("404");
});
