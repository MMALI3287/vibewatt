import { expect, test, type Page } from "@playwright/test";

// d3a probe (run 3): quota visibility on empty filters, odd dates, overflow on phase 5-6 pages, cursor paging.
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

async function mockQuota(page: Page) {
  await page.route("**/api/quota**", r => r.fulfill({ json: { source: "probe", fetched_at: "2026-09-22T00:00:00Z", windows: [
    { label: "5-hour", utilization: 42, resets_at: null }, { label: "7-day", utilization: 64, resets_at: null }] } }));
}

test("account-wide quota meters stay visible when filters match no local usage", async ({ page }) => {
  await mockQuota(page);
  await page.goto("/");
  await expect(page.getByLabel("Plan utilization (account-wide)")).toContainText("5-hour 42%");
  await page.goto("/?from=2030-01-01");
  await expect(page.getByRole("heading", { name: "No usage for these filters" })).toBeVisible();
  const meters = await page.getByLabel("Plan utilization (account-wide)").count();
  console.log("meters rendered on empty filtered Overview:", meters);
  expect.soft(meters, "plan utilization (account-wide, filter-independent) disappears when local filters are empty").toBe(1);
});

test("shape-valid but impossible date and reversed range", async ({ page }) => {
  await page.goto("/?from=2026-13-45");
  await page.waitForTimeout(1500);
  const main = (await page.locator("main").innerText()).slice(0, 160).replace(/\n/g, " | ");
  const fromValue = await page.locator('input[name="from"]').inputValue();
  console.log("from=2026-13-45 main:", main, "| from input value:", JSON.stringify(fromValue));
  await page.goto("/?from=2026-09-30&to=2026-09-01");
  await page.waitForTimeout(1500);
  console.log("reversed range main:", (await page.locator("main").innerText()).slice(0, 160).replace(/\n/g, " | "));
});

for (const width of [390, 768]) {
  test(`analysis and wrapped have no page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/analysis");
    await expect(page.getByRole("heading", { name: "Analysis", level: 1 })).toBeVisible();
    for (const d of await page.locator("details").all()) await d.evaluate(e => (e as HTMLDetailsElement).open = true);
    const a = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await page.goto("/wrapped");
    await expect(page.getByRole("heading", { name: /Wrapped/ })).toBeVisible();
    for (const d of await page.locator("details").all()) await d.evaluate(e => (e as HTMLDetailsElement).open = true);
    const w = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    console.log(`overflow at ${width}: analysis ${a}, wrapped ${w}`);
    expect.soft(a).toBe(0);
    expect.soft(w).toBe(0);
  });
}

test("tokens metric: hero and KPI match API token total", async ({ page }) => {
  const s = await (await page.request.get("/api/summary?source=all&metric=tokens")).json();
  const t = s.total;
  const tokens = t.input + t.cache_write_5m + t.cache_write_1h + t.cache_read + t.output;
  await page.goto("/?metric=tokens");
  await expect(page.getByTestId("hero-number")).toHaveText(`${compact.format(tokens)} tokens`);
  await expect(page.getByTestId("kpi-tokens")).toHaveText(compact.format(tokens));
});

test("second sessions page is requested with the cursor of the last row", async ({ page }) => {
  const first = await (await page.request.get("/api/sessions?source=all&metric=cost&limit=40")).json();
  const lastCursor = first.at(-1).cursor;
  await page.goto("/sessions");
  const table = page.getByRole("table", { name: "Session list" });
  await expect(table.locator("tbody tr")).toHaveCount(40);
  const req = page.waitForRequest(r => r.url().includes("/api/sessions?") && r.url().includes("cursor="));
  await page.getByRole("button", { name: "Load more sessions" }).scrollIntoViewIfNeeded();
  const url = new URL((await req).url());
  console.log("page-2 cursor", url.searchParams.get("cursor"), "expected", lastCursor);
  expect(url.searchParams.get("cursor")).toBe(lastCursor);
  // harvested row cost is the API cost
  await expect(table.locator("tbody tr", { hasText: "Cloud session 03" })).toContainText("$1.23");
});
