import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/status", route => route.fulfill({ status: 503, body: "unavailable" }));
});

test("status failure never blocks dashboard and disabled AI summary stays local", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toBeVisible();
  await expect(page.getByRole("region", { name: "Anthropic service status" })).toHaveCount(0);
  await page.getByText("AI weekly summary", { exact: true }).click();
  await page.getByRole("button", { name: "Generate weekly summary" }).click();
  await expect(page.getByText("AI summaries are disabled.", { exact: true })).toBeVisible();
});

test("Wrapped year, filters, share card and empty state", async ({ page }) => {
  await page.goto("/wrapped?year=2026");
  await expect(page.getByRole("heading", { name: "2026 Wrapped" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model mix over time" })).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download share card" }).click();
  expect((await downloaded).suggestedFilename()).toBe("vibewatt-wrapped-2026.svg");
  await page.reload();
  await expect(page.getByLabel("Wrapped year")).toHaveValue("2026");
  await page.goto("/wrapped?year=2000");
  await expect(page.getByText("No stored usage for this year and selection. Sync local logs or choose another year.")).toBeVisible();
});

for (const width of [1440, 1024, 768, 390]) {
  test(`Wrapped and expanded tools fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/wrapped?year=2026");
    await expect(page.getByRole("heading", { name: "2026 Wrapped" })).toBeVisible();
    await page.locator("details").evaluateAll(elements => elements.forEach(element => element.setAttribute("open", "")));
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}

test("Wrapped loading and error states", async ({ page }) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/wrapped**", async route => {
    await pending;
    await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"fixture failure"}' });
  });
  await page.goto("/wrapped?year=2026");
  await expect(page.getByText("Loading year in review…")).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "Could not load Wrapped" })).toBeVisible({ timeout: 15000 });
});

test("Wrapped year accepts a typed four-digit year", async ({ page }) => {
  // A-051: typing used to commit the first digit and unmount the input.
  await page.goto("/wrapped?year=2026");
  const input = page.getByLabel("Wrapped year");
  await input.fill("2025");
  await input.press("Enter");
  await expect(page).toHaveURL(/year=2025/);
  await expect(input).toHaveValue("2025");
  await expect(page.getByText("No project usage this year.")).toBeVisible();
});

test("Overview explains why numbers differ from Claude's Stats", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Why these numbers differ from Claude's Stats").click();
  const table = page.getByRole("table", { name: "vibewatt compared with Claude's Stats" });
  await expect(table).toContainText("Input + output tokens");
  await expect(page.getByText("Session:", { exact: false })).toBeVisible();
});
