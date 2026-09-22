import { expect, test, type Page } from "@playwright/test";

// d3a probe: loading / empty / error states for every fetching component.
const fail = (page: Page, glob: string) => page.route(glob, r => r.fulfill({ status: 503, json: { detail: "probe-down" } }));
const slow = (page: Page, glob: string, ms = 2500) => page.route(glob, async r => { await new Promise(res => setTimeout(res, ms)); await r.continue(); });

test.describe("error states", () => {
  test("footer health error", async ({ page }) => {
    await fail(page, "**/api/health");
    await page.goto("/");
    await expect(page.locator("footer")).toContainText("Store status unavailable", { timeout: 10000 });
  });
  test("hero quota error", async ({ page }) => {
    await fail(page, "**/api/quota**");
    await page.goto("/");
    await expect(page.getByLabel("Plan utilization (account-wide)")).toContainText("Plan utilization unavailable", { timeout: 10000 });
  });
  test("overview summary error", async ({ page }) => {
    await fail(page, "**/api/summary**");
    await page.goto("/");
    await expect(page.getByRole("alert").filter({ hasText: "Could not load usage" })).toBeVisible({ timeout: 10000 });
  });
  test("blocks error", async ({ page }) => {
    await fail(page, "**/api/blocks**");
    await page.goto("/");
    await expect(page.getByRole("alert").filter({ hasText: "Could not load blocks" })).toBeVisible({ timeout: 10000 });
  });
  test("alerts error", async ({ page }) => {
    await fail(page, "**/api/alerts**");
    await page.goto("/");
    await page.getByText("Burn and spike alerts").click();
    await expect(page.getByRole("alert").filter({ hasText: "Could not load alerts" })).toBeVisible({ timeout: 10000 });
  });
  test("facets error", async ({ page }) => {
    await fail(page, "**/api/session-facets**");
    await page.goto("/");
    await expect(page.getByRole("alert").filter({ hasText: "Stored filter choices unavailable" })).toBeVisible({ timeout: 10000 });
  });
  test("breakdown error", async ({ page }) => {
    await fail(page, "**/api/summary**");
    await page.goto("/models");
    await expect(page.getByRole("alert").filter({ hasText: "Could not load models" })).toBeVisible({ timeout: 10000 });
  });
  test("session modal error", async ({ page }) => {
    await fail(page, "**/api/sessions/s1");
    await page.goto("/sessions/s1");
    await expect(page.getByRole("dialog")).toContainText("Could not load session", { timeout: 10000 });
  });
  test("concierge error", async ({ page }) => {
    await fail(page, "**/api/concierge**");
    await page.goto("/?project=demo");
    await page.getByText("Project resume brief").click();
    await page.getByRole("button", { name: /Prepare brief for demo/ }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Could not prepare brief" })).toBeVisible({ timeout: 10000 });
  });
  test("weekly summary error", async ({ page }) => {
    await fail(page, "**/api/weekly-summary**");
    await page.goto("/");
    await page.getByText("AI weekly summary").click();
    await page.getByRole("button", { name: "Generate weekly summary" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Could not generate summary" })).toBeVisible({ timeout: 10000 });
  });
  test("filter-bar options: unfiltered summary failure is reported", async ({ page }) => {
    // Fail only the unfiltered options query (no project= param); the filtered page query still works.
    await page.route("**/api/summary**", r => r.request().url().includes("project=") ? r.continue()
      : r.fulfill({ status: 503, json: { detail: "probe-down" } }));
    await fail(page, "**/api/session-facets**");
    await page.goto("/projects?project=demo");
    await expect(page.getByTestId("kpi-responses")).toHaveText("2");
    await page.waitForTimeout(3000);
    const opts = await page.locator('select[name="model"] option').allTextContents();
    const srcOpts = await page.locator('select[name="source"] option').allTextContents();
    const alerts = await page.getByRole("alert").allTextContents();
    console.log("model options", opts, "source options", srcOpts, "alerts", alerts);
  });
  test("filter-bar options: summary fails, facets ok -> no error shown", async ({ page }) => {
    await page.route("**/api/summary**", r => r.request().url().includes("project=") ? r.continue()
      : r.fulfill({ status: 503, json: { detail: "probe-down" } }));
    await page.goto("/projects?project=demo");
    await expect(page.getByTestId("kpi-responses")).toHaveText("2");
    await page.waitForTimeout(3000);
    const alerts = await page.getByRole("alert").allTextContents();
    console.log("alerts (facets ok, options summary failed)", alerts);
  });
  test("400 detail from API is readable", async ({ page }) => {
    await page.goto("/?from=2026-13-45");
    const alert = page.getByRole("alert").filter({ hasText: "Could not load usage" });
    await expect(alert).toBeVisible({ timeout: 10000 });
    console.log("error text:", await alert.textContent());
    await expect(alert).toContainText("from expects YYYY-MM-DD");
  });
  test("422 validation detail is readable, not [object Object]", async ({ page }) => {
    await page.route("**/api/summary**", r => r.fulfill({ status: 422, json: { detail: [{ loc: ["query", "from"], msg: "bad date", type: "value_error" }] } }));
    await page.goto("/");
    const alert = page.getByRole("alert").filter({ hasText: "Could not load usage" });
    await expect(alert).toBeVisible({ timeout: 10000 });
    const text = (await alert.textContent()) ?? "";
    console.log("422 error text:", text);
    expect.soft(text).not.toContain("[object Object]");
  });
});

test.describe("loading states", () => {
  test("overview and footer loading", async ({ page }) => {
    await slow(page, "**/api/**");
    await page.goto("/");
    await expect(page.getByText("Loading usage…")).toBeVisible();
    await expect(page.getByText("Checking data freshness…")).toBeVisible();
  });
  test("sessions, breakdown, analysis, wrapped, modal loading", async ({ page }) => {
    await slow(page, "**/api/**");
    await page.goto("/sessions");
    await expect(page.getByText("Loading sessions…")).toBeVisible();
    await page.goto("/projects");
    await expect(page.getByText("Loading projects…")).toBeVisible();
    await page.goto("/analysis");
    await expect(page.getByText("Analyzing stored usage…")).toBeVisible();
    await page.goto("/wrapped");
    await expect(page.getByText("Loading year in review…")).toBeVisible();
    await page.goto("/sessions/s1");
    await expect(page.getByText("Loading session…")).toBeVisible();
  });
  test("quota and blocks loading", async ({ page }) => {
    await slow(page, "**/api/quota**");
    await slow(page, "**/api/blocks**");
    await page.goto("/");
    await expect(page.getByText("Loading plan utilization…")).toBeVisible();
    await expect(page.getByText("Loading blocks…")).toBeVisible();
  });
});

test.describe("empty states", () => {
  test("every view has an empty state for a future range", async ({ page }) => {
    await page.goto("/?from=2030-01-01");
    await expect(page.getByText("No usage for these filters")).toBeVisible();
    await page.goto("/sessions?from=2030-01-01");
    await expect(page.getByText("No sessions match.", { exact: false })).toBeVisible();
    await page.goto("/projects?from=2030-01-01");
    await expect(page.getByText("No projects for these filters.")).toBeVisible();
    await page.goto("/models?from=2030-01-01");
    await expect(page.getByText("No models for these filters.")).toBeVisible();
    await page.goto("/wrapped?year=2030");
    await expect(page.getByText("No stored usage for this year", { exact: false })).toBeVisible();
    await page.goto("/analysis?from=2030-01-01&source=nope");
    await expect(page.getByText("No findings for this selection")).toBeVisible();
  });
  test("wrapped top-projects / model-mix tables have an empty message", async ({ page }) => {
    await page.goto("/wrapped?year=2030");
    await expect(page.getByText("No stored usage for this year", { exact: false })).toBeVisible();
    const top = page.locator("section.card", { hasText: "Top projects by tokens" });
    const rows = await top.locator("tbody tr").count();
    const text = (await top.textContent()) ?? "";
    console.log("wrapped top projects rows", rows, "text", text);
    expect.soft(text, "empty top-projects table has no empty message").toMatch(/No |none/i);
  });
  test("quota with zero windows shows something", async ({ page }) => {
    await page.route("**/api/quota**", r => r.fulfill({ json: { source: "probe", fetched_at: "2026-09-22T00:00:00Z", windows: [] } }));
    await page.goto("/");
    await expect(page.getByTestId("kpi-responses")).toHaveText("3");
    const txt = (await page.getByLabel("Plan utilization (account-wide)").textContent()) ?? "";
    console.log("meters text with windows=[]:", JSON.stringify(txt));
    expect.soft(txt.trim().length, "meters area blank for empty quota windows").toBeGreaterThan(0);
  });
  test("footer with no synced logs", async ({ page }) => {
    await page.route("**/api/health", r => r.fulfill({ json: { turns: { n: 0, lo: null, hi: null }, cloud: {}, cloud_by_surface: [], prompts: {}, last_harvest: null, note: "" } }));
    await page.goto("/");
    await expect(page.locator("footer")).toContainText("No local logs synced yet");
  });
});
