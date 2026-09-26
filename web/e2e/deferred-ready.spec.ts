import { expect, test } from "@playwright/test";

test("date presets use the server report day and preserve other filters", async ({ page }) => {
  await page.route("**/api/report-context", route => route.fulfill({ json: {
    today: "2024-03-03", timezone: "America/New_York", day_start_hour: 4, as_of: null,
  } }));
  await page.goto("/?source=claude-code&metric=tokens");
  await page.getByLabel("Date preset", { exact: true }).selectOption("week");
  await expect(page.locator('input[name="from"]')).toHaveValue("2024-02-26");
  await expect(page.locator('input[name="to"]')).toHaveValue("2024-03-03");
  await expect(page.locator('select[name="source"]')).toHaveValue("claude-code");
  await expect(page.locator('select[name="metric"]')).toHaveValue("tokens");
  await page.reload();
  await expect(page.locator('input[name="from"]')).toHaveValue("2024-02-26");
  await page.getByLabel("Date preset", { exact: true }).selectOption("all");
  await expect(page.locator('input[name="from"]')).toHaveValue("");
});

test("header slash shortcut searches sessions while preserving filters", async ({ page }) => {
  await page.goto("/?source=claude-code");
  await page.keyboard.press("/");
  const search = page.getByRole("searchbox", { name: "Search all sessions" });
  await expect(search).toBeFocused();
  await search.fill("fixture/");
  await page.keyboard.press("/");
  await expect(search).toHaveValue("fixture//");
  await search.fill("fixture");
  await search.press("Enter");
  await expect(page).toHaveURL(/\/sessions\?source=claude-code&q=fixture/);
  await expect(page.getByRole("searchbox", { name: "Search sessions", exact: true })).toHaveValue("fixture");
});

test("overview explains its monthly comparison and local cost provenance", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toBeVisible();
  await expect(page.getByRole("region", { name: "API-equivalent versus plan" })).toContainText("Date filters do not apply");
  await expect(page.locator(".provenance").first()).toContainText("Estimated API-equivalent");
});

test("visible live refresh makes one store cycle without external status calls", async ({ page }) => {
  await page.clock.install();
  const requests: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) requests.push(path);
  });
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toBeVisible();
  await expect(page.locator(".footer")).not.toContainText("Checking data freshness");
  requests.length = 0;
  await page.clock.fastForward(60_000);
  await expect.poll(() => requests.filter(path => path === "/api/summary").length).toBe(1);
  await expect.poll(() => requests.filter(path => path === "/api/quota").length).toBe(1);
  expect(requests).not.toContain("/api/status");
  expect(requests).not.toContain("/api/weekly-summary");
  expect(requests).not.toContain("/api/sync");
});

test("URL-backed search keeps the caret when typing mid-value", async ({ page }) => {
  await page.goto("/sessions");
  const search = page.getByRole("searchbox", { name: "Search sessions", exact: true });
  await search.click();
  await page.keyboard.type("abcd");
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.type("XY");
  await expect(search).toHaveValue("aXYbcd");
  await expect(page).toHaveURL(/q=aXYbcd/);
});
