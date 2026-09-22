import { expect, test } from "@playwright/test";

// d3a probe (run 3): sticky filter bar, unknown session deep link, footer coverage wording vs summary.
test("filter bar stays pinned to the top after scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toBeVisible();
  await page.mouse.wheel(0, 1500);
  await page.waitForTimeout(300);
  const scrollY = await page.evaluate(() => window.scrollY);
  const box = await page.getByRole("form", { name: "Filters" }).boundingBox();
  console.log("scrollY", scrollY, "filter bar y", box?.y);
  expect(scrollY).toBeGreaterThan(100);
  expect(Math.abs(box!.y)).toBeLessThan(2);
});

test("unknown session deep link shows an error inside the modal", async ({ page }) => {
  await page.goto("/sessions/does-not-exist");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toContainText("Could not load session", { timeout: 10000 });
  console.log("unknown session:", await dialog.getByRole("alert").innerText());
});

test("footer coverage wording vs what the Overview figures include", async ({ page }) => {
  const s = await (await page.request.get("/api/summary?source=all&metric=cost")).json();
  const sessions = await (await page.request.get("/api/sessions?source=all&metric=cost&limit=100")).json();
  const harvestedCost = sessions.filter((r: { harvested: boolean }) => r.harvested).reduce((a: number, r: { cost: number }) => a + r.cost, 0);
  await page.goto("/");
  const footer = await page.locator("footer").innerText();
  console.log("summary cost", s.total.cost_usd, "harvested session cost", harvestedCost.toFixed(2), "| footer:", footer.replace(/\n/g, " | "));
});
