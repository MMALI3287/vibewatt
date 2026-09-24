import { expect, test } from "@playwright/test";

test("analysis groups start collapsed and repeated-read findings survive refresh and dismissal", async ({ page }) => {
  await page.goto("/analysis?source=claude-code&project=demo");
  await expect(page.getByRole("heading", { name: "Analysis", exact: true })).toBeVisible();
  await expect(page.locator(".analysis-group[open]")).toHaveCount(0);
  await page.locator("summary").filter({ hasText: "Token-waste checks" }).click();
  const finding = page.getByRole("article", { name: "Repeated file reads" });
  await expect(finding).toContainText("Local logs only");
  await finding.getByRole("link", { name: "View session" }).click();
  await expect(page.getByRole("dialog")).toContainText("fix the sync");
  await page.goBack();
  await finding.getByRole("button", { name: "Dismiss finding" }).click();
  await expect(finding).toHaveCount(0);
  await page.reload();
  await page.locator("summary").filter({ hasText: "Token-waste checks" }).click();
  await expect(finding).toHaveCount(0);
  // Router 7 commits URL state in a transition; assert its eventual controlled state.
  await page.getByRole("checkbox", { name: "Show dismissed" }).click();
  await expect(page.getByRole("checkbox", { name: "Show dismissed" })).toBeChecked();
  // Changing this query briefly unmounts the groups while loading.
  await page.locator("summary").filter({ hasText: "Token-waste checks" }).click();
  await expect(finding).toContainText("Dismissed");
  await finding.getByRole("button", { name: "Restore finding" }).click();
  await expect(finding).not.toContainText("Dismissed");
  await page.getByRole("button", { name: "Refresh analysis" }).click();
  await expect(finding).toBeVisible();
});

test("analysis filters restore from URL and context warning is a harvested snapshot", async ({ page }) => {
  await page.goto("/analysis?source=web&project=cloud-project&from=2026-09-16&to=2026-09-16");
  await page.locator("summary").filter({ hasText: "Context windows" }).click();
  const card = page.getByRole("article", { name: "Context window nearing capacity" });
  await expect(card).toContainText("urgent");
  await expect(card).toContainText("Harvested snapshot");
  await page.reload();
  await page.locator("summary").filter({ hasText: "Context windows" }).click();
  await expect(card).toBeVisible();
  await page.getByLabel("Severity", { exact: true }).selectOption("info");
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No findings for this selection" })).toBeVisible();
  await page.locator("summary").filter({ hasText: "Coverage and interpretation" }).click();
  await expect(page.getByText("Peak-window checks are unavailable", { exact: false })).toBeVisible();
});

test("analysis handles loading empty errors and failed dismissals", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/findings?**", async route => { await held; await route.continue(); });
  await page.goto("/analysis?project=absent");
  await expect(page.getByText("Analyzing stored usage…")).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "No findings for this selection" })).toBeVisible();
  await page.unroute("**/api/findings?**");
  await page.route("**/api/findings?**", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await page.goto("/analysis");
  await expect(page.getByRole("alert").filter({ hasText: "Could not load analysis" })).toBeVisible();
  await page.unroute("**/api/findings?**");
  await page.getByRole("button", { name: "Retry" }).click();
  await page.locator("summary").filter({ hasText: "Token-waste checks" }).click();
  await page.route("**/api/findings/*/dismiss", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  const card = page.getByRole("article", { name: "Repeated file reads" });
  await card.getByRole("button", { name: "Dismiss finding" }).click();
  await expect(card.getByRole("alert")).toContainText("Could not save dismissal");
  await expect(card).toBeVisible();
});

for (const width of [1440, 1024, 768, 390]) {
  test(`analysis expanded evidence fits ${width}px in both themes`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/analysis");
    await expect(page.locator(".analysis-group")).toHaveCount(6);
    for (const summary of await page.locator(".analysis-page summary").all()) await summary.click();
    for (const theme of ["light", "dark"] as const) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
      await expect(page.getByRole("article", { name: "Repeated file reads" })).toBeVisible();
    }
  });
}

test("finding details deep-link and survive reload; severity lives in the URL", async ({ page }) => {
  // A-046: a route-backed finding modal. A-123: severity and dismissed in the URL.
  await page.goto("/analysis?source=claude-code&project=demo&severity=info&dismissed=1");
  await expect(page.getByLabel("Severity")).toHaveValue("info");
  await page.reload();
  await expect(page.getByLabel("Severity")).toHaveValue("info");
  await expect(page.getByRole("checkbox", { name: "Show dismissed" })).toBeChecked();
  await page.goto("/analysis?source=claude-code&project=demo");
  await page.locator("summary").filter({ hasText: "Token-waste checks" }).click();
  const finding = page.getByRole("article", { name: "Repeated file reads" });
  await finding.getByRole("link", { name: "Details" }).click();
  await expect(page).toHaveURL(/\/analysis\/findings\/[0-9a-f]+/);
  await expect(page.getByRole("dialog")).toContainText("Repeated file reads");
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Repeated file reads");
  await page.getByRole("button", { name: "Close finding" }).click();
  await expect(page).toHaveURL(/\/analysis\?/);
});
