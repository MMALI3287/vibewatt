import { expect, test } from "@playwright/test";

// d3a probe: rendered numbers vs API values on the fixture server.
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

test("KPI row equals /api/summary (formatted)", async ({ page }) => {
  const s = await (await page.request.get("/api/summary?source=all&metric=cost")).json();
  await page.goto("/");
  const t = s.total;
  const tokens = t.input + t.cache_write_5m + t.cache_write_1h + t.cache_read + t.output;
  console.log("API total cost_usd", t.cost_usd, "tokens", tokens, "responses", t.responses, "sessions", s.sessions);
  await expect(page.getByTestId("kpi-responses")).toHaveText(String(t.responses));
  await expect(page.getByTestId("kpi-sessions")).toHaveText(String(s.sessions));
  await expect(page.getByTestId("kpi-tokens")).toHaveText(compact.format(tokens));
  await expect(page.getByTestId("kpi-cache-hit-rate")).toHaveText(`${Math.round(s.cache_hit_rate * 100)}%`);
  await expect(page.getByTestId("kpi-cost")).toHaveText(usd.format(t.cost_usd));
  console.log("hero:", await page.getByTestId("hero-number").textContent(), "kpi-cost:", await page.getByTestId("kpi-cost").textContent());
});

test("non-zero cost is never rendered as $0.00 (hero, KPI)", async ({ page }) => {
  const s = await (await page.request.get("/api/summary?source=all&metric=cost")).json();
  expect(s.total.cost_usd).toBeGreaterThan(0);
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  const hero = (await page.getByTestId("hero-number").textContent()) ?? "";
  const kpi = (await page.getByTestId("kpi-cost").textContent()) ?? "";
  console.log(`API cost ${s.total.cost_usd} -> hero "${hero}" kpi "${kpi}"`);
  expect.soft(hero, "hero shows zero for a non-zero API cost").not.toBe("$0.00");
  expect.soft(kpi, "KPI shows zero for a non-zero API cost").not.toBe("$0.00");
});

test("session turn costs below one cent are distinguishable from zero", async ({ page }) => {
  const d = await (await page.request.get("/api/sessions/s1")).json();
  console.log("API turn costs", d.turns.map((x: { cost: number }) => x.cost), "session cost", d.cost);
  await page.goto("/sessions/s1");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("table", { name: "Session turns" })).toBeVisible();
  const costCells = await dialog.locator("table tbody tr td:last-child").allTextContents();
  const sessionCost = await dialog.locator("dt:has-text('Cost') + dd").textContent();
  console.log("rendered turn costs", costCells, "rendered session cost", sessionCost);
  for (const c of costCells) expect.soft(c, "priced non-zero turn rendered as $0.00").not.toBe("$0.00");
  expect.soft(sessionCost).not.toBe("$0.00");
});

test("blocks and wrapped small costs", async ({ page }) => {
  await page.goto("/");
  const blocks = page.getByRole("table", { name: "Blocks" });
  await expect(blocks).toBeVisible();
  const cells = await blocks.locator("tbody tr td:nth-child(5)").allTextContents();
  console.log("rendered block costs", cells);
  for (const c of cells) expect.soft(c).not.toBe("$0.00");
  await page.goto("/wrapped");
  const live = await page.locator("dt:has-text('Live local-log cost') + dd").textContent();
  console.log("wrapped live local-log cost", live);
  expect.soft(live).not.toBe("$0.00");
});
