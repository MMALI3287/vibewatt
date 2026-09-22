import { expect, test, type Page } from "@playwright/test";

// d3a probe: heatmap placement, table pagination, search facets, quota meters.
const bucket = (cost: number, input: number) => ({ responses: 1, input, cache_write_5m: 0, cache_write_1h: 0, cache_read: 0, output: 0, thinking: 0, web_searches: 0, cost_usd: cost });

async function mockSummary(page: Page, days: number) {
  await page.route("**/api/summary**", async r => {
    const real = await (await r.fetch()).json();
    const by_day: Record<string, ReturnType<typeof bucket>> = {};
    const start = Date.UTC(2026, 8, 1); // 2026-09-01 is a Tuesday
    for (let i = 0; i < days; i++) {
      const d = new Date(start + i * 86400000).toISOString().slice(0, 10);
      by_day[d] = bucket(0.5 + i, 100 + i);
    }
    await r.fulfill({ json: { ...real, by_day, total: { ...real.total, responses: days } } });
  });
}

test("heatmap puts Sundays in the first row and weeks in columns", async ({ page }) => {
  await mockSummary(page, 21);
  await page.goto("/");
  const cell = (d: string) => page.locator(`.heat-cell[aria-label^="${d}:"]`);
  await expect(cell("2026-09-01")).toBeVisible();
  const box = async (d: string) => (await cell(d).boundingBox())!;
  const tue1 = await box("2026-09-01");
  const sun6 = await box("2026-09-06");
  const mon7 = await box("2026-09-07");
  const tue8 = await box("2026-09-08");
  const sat5 = await box("2026-09-05");
  console.log({ tue1, sun6, mon7, tue8, sat5 });
  expect(sun6.y).toBeLessThan(tue1.y);         // Sunday is the top row
  expect(tue8.y).toBeCloseTo(tue1.y, 0);       // same weekday, same row
  expect(tue8.x).toBeGreaterThan(tue1.x);      // next week, next column
  expect(sat5.y).toBeGreaterThan(tue1.y);      // Saturday is the bottom row
  // tooltip on focus
  await cell("2026-09-06").focus();
  await expect(cell("2026-09-06").getByRole("tooltip")).toBeVisible();
});

test("tables elsewhere are paginated (daily table on Overview, by-model table)", async ({ page }) => {
  await mockSummary(page, 60);
  await page.goto("/");
  const daily = page.locator("section[aria-label='Daily cost']");
  await daily.locator("summary").click();
  const rows = await daily.locator("tbody tr").count();
  const pager = await daily.locator(".pager").count();
  console.log("Daily cost table rows", rows, "pager", pager);
  const heatRows = page.locator("section[aria-label='Daily cost heatmap']");
  await heatRows.locator("summary").click();
  console.log("Heatmap table rows", await heatRows.locator("tbody tr").count(), "pager", await heatRows.locator(".pager").count());
  expect.soft(rows, "Overview daily table renders every day with no pagination").toBeLessThanOrEqual(20);
});

test("session search matches project and model names", async ({ page }) => {
  await page.goto("/sessions?q=cloud-project");
  const table = page.getByRole("table", { name: "Session list" });
  await expect(table.locator("tbody tr").first()).toBeVisible();
  const byProject = await table.locator("tbody tr").count();
  await page.goto("/sessions?q=cloud-only-model");
  await expect(table.locator("tbody tr").first()).toBeVisible();
  const byModel = await table.locator("tbody tr").count();
  console.log("search by project rows", byProject, "by model rows", byModel);
  expect(byModel).toBe(1);
});

test("quota meters render percentages, clamp and stay filter-independent", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/quota**", r => { calls++; return r.fulfill({ json: { source: "probe", fetched_at: "2026-09-22T00:00:00Z", windows: [
    { label: "5-hour", utilization: 42.4, resets_at: null }, { label: "7-day", utilization: 130, resets_at: null }] } }); });
  await page.goto("/");
  const meters = page.getByLabel("Plan utilization (account-wide)");
  await expect(meters).toContainText("5-hour 42%");
  await expect(meters).toContainText("7-day 100%");
  await page.locator('select[name="project"]').selectOption("demo");
  await expect(page.getByTestId("kpi-responses")).toHaveText("2");
  await expect(meters).toContainText("5-hour 42%");
  console.log("quota calls after filter change", calls);
});
