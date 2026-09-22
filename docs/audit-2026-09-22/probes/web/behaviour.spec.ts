import { expect, test, type Page } from "@playwright/test";

// d3a probe: navigation, modal, theme, invalid params, sync, long titles.
const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test("theme choice persists across reload", async ({ page }) => {
  await page.goto("/");
  const btn = page.getByRole("button", { name: /^Theme:/ });
  await expect(btn).toHaveText("Theme: system");
  await btn.click();
  await btn.click();
  await expect(btn).toHaveText("Theme: dark");
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  await page.reload();
  await expect(page.getByRole("button", { name: /^Theme:/ })).toHaveText("Theme: dark");
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  console.log("dark body bg", bg);
});

test("light override wins over OS dark", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.getByRole("button", { name: /^Theme:/ }).click(); // system -> light
  const lightBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  console.log("os-dark system bg", darkBg, "forced light bg", lightBg);
  expect(lightBg).not.toBe(darkBg);
});

test("modal traps focus and makes the filter bar inert", async ({ page }) => {
  await page.goto("/sessions?q=fix%20the%20sync");
  await page.getByRole("link", { name: "fix the sync" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("table", { name: "Session turns" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close session" })).toBeFocused();
  const outside: string[] = [];
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const where = await page.evaluate(() => {
      const a = document.activeElement;
      if (!a || a === document.body) return "body";
      return a.closest("dialog") ? "in" : `${a.tagName}.${a.className}:${a.textContent?.slice(0, 20)}`;
    });
    if (where !== "in" && where !== "body") outside.push(where);
  }
  console.log("focus escaped to", outside);
  expect(outside).toEqual([]);
  // Filter bar must not be operable behind the modal.
  const before = page.url();
  await page.locator('select[name="metric"]').selectOption("tokens", { force: true, timeout: 2000 }).catch(e => console.log("select blocked:", String(e).slice(0, 80)));
  console.log("url before", before, "after", page.url());
});

test("Back walks filter history and KPI follows", async ({ page }) => {
  await page.goto("/projects");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  await page.locator('select[name="project"]').selectOption("demo");
  await expect(page.getByTestId("kpi-responses")).toHaveText("2");
  await page.locator('select[name="metric"]').selectOption("tokens");
  await expect(page).toHaveURL(/metric=tokens/);
  await page.goBack();
  await expect(page).not.toHaveURL(/metric=tokens/);
  await expect(page.locator('select[name="metric"]')).toHaveValue("cost");
  await expect(page.locator('select[name="project"]')).toHaveValue("demo");
  await expect(page.getByTestId("kpi-responses")).toHaveText("2");
  await page.goBack();
  await expect(page.locator('select[name="project"]')).toHaveValue("");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
});

test("modal: Back closes, Forward reopens over the same background", async ({ page }) => {
  await page.goto("/analysis");
  await page.getByText("Token-waste checks").click();
  await page.getByRole("link", { name: "View session" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Analysis", level: 1 })).toBeAttached();
  await page.goBack();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/analysis/);
  await page.goForward();
  await expect(page.getByRole("dialog")).toBeVisible();
  const bgHeading = await page.locator("main h1").first().textContent();
  console.log("background heading after Forward:", bgHeading);
  expect(bgHeading).toBe("Analysis");
});

test("breadcrumb inside modal reads Projects / X / title and navigates", async ({ page }) => {
  await page.goto("/sessions/s1?metric=tokens");
  const crumbs = page.getByRole("dialog").getByRole("navigation", { name: "Breadcrumbs" });
  await expect(crumbs).toHaveText(/Projects\s*\/\s*demo\s*\/\s*fix the sync/);
  await crumbs.getByRole("link", { name: "demo" }).click();
  await expect(page).toHaveURL(/\/projects\?.*project=demo/);
  await expect(page).toHaveURL(/metric=tokens/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("invalid URL params degrade gracefully", async ({ page }) => {
  await page.goto("/?metric=bogus");
  await expect(page.locator('select[name="metric"]')).toHaveValue("cost");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  await page.goto("/?from=garbage&to=2026-99");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  await page.goto("/?source=bogus");
  await expect(page.getByText("No usage for these filters")).toBeVisible();
  await expect(page.locator('select[name="source"]')).toHaveValue("bogus");
  await page.goto("/wrapped?year=abc");
  await expect(page.getByText("Invalid year")).toBeVisible();
  await page.goto("/?from=2026-09-20&to=2026-09-01");
  await expect(page.getByText("No usage for these filters")).toBeVisible();
  console.log("inverted range state:", await page.locator("main").textContent());
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  console.log("404 route text:", await page.locator("main section.state").textContent());
});

test("sync posts, invalidates queries and reports failure", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  const refetched: string[] = [];
  page.on("request", r => { if (r.method() === "GET" && r.url().includes("/api/")) refetched.push(new URL(r.url()).pathname); });
  const post = page.waitForResponse(r => r.url().endsWith("/api/sync") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Sync" }).click();
  const res = await post;
  console.log("sync status", res.status(), await res.text());
  await page.waitForTimeout(1500);
  console.log("GETs after sync", [...new Set(refetched)]);
  expect(refetched).toContain("/api/summary");
  await expect(page.getByRole("button", { name: "Sync" })).toBeVisible();
  await page.route("**/api/sync", r => r.fulfill({ status: 500, json: { detail: "boom" } }));
  await page.getByRole("button", { name: "Sync" }).click();
  await expect(page.getByRole("button", { name: "Sync failed" })).toBeVisible();
});

test("very long session title at 390px does not overflow page or dialog", async ({ page }) => {
  const long = "refactor-" + "x".repeat(300) + " and " + "averyveryverylongwordwithoutbreaks".repeat(8);
  await page.route("**/api/sessions?**", async r => {
    const res = await r.fetch();
    const rows = await res.json();
    if (rows.length) rows[0].title = long;
    await r.fulfill({ json: rows });
  });
  await page.route("**/api/sessions/s1", async r => {
    const res = await r.fetch();
    const d = await res.json();
    d.title = long; d.project = "p-" + "y".repeat(200);
    await r.fulfill({ json: d });
  });
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/sessions");
  await expect(page.getByRole("table", { name: "Session list" })).toBeVisible();
  const o1 = await overflow(page);
  await page.goto("/sessions/s1");
  await expect(page.getByRole("dialog").getByRole("table", { name: "Session turns" })).toBeVisible();
  const d = await page.getByRole("dialog").evaluate(el => ({ sw: el.scrollWidth, cw: el.clientWidth, w: el.getBoundingClientRect().width }));
  const o2 = await overflow(page);
  console.log("page overflow list", o1, "page overflow modal", o2, "dialog", d);
  expect.soft(o1).toBe(0);
  expect.soft(o2).toBe(0);
  expect.soft(d.sw - d.cw, "dialog content wider than dialog").toBeLessThanOrEqual(0);
});

test("wrapped year can be typed key by key", async ({ page }) => {
  await page.goto("/wrapped");
  const input = page.getByRole("spinbutton", { name: "Wrapped year" });
  await expect(input).toHaveValue("2026");
  await input.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("2025", { delay: 150 });
  await page.waitForTimeout(1000);
  console.log("url after typing 2025:", page.url());
  const still = await page.getByRole("spinbutton", { name: "Wrapped year" }).count();
  console.log("year input present:", still, still ? await page.getByRole("spinbutton", { name: "Wrapped year" }).inputValue() : "");
  expect.soft(page.url()).toContain("year=2025");
});

test("models drill-down embedded sessions heading", async ({ page }) => {
  await page.goto("/models?model=claude-opus-5");
  await expect(page.getByRole("table", { name: "Session list" })).toBeVisible();
  const h = await page.locator("section[aria-label='Sessions'] h2").textContent();
  console.log("embedded heading on models drill-down:", h);
  expect.soft(h).not.toBe("Project sessions");
});

test("slash does not focus a search box (deferred feature check)", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toHaveText("3");
  await page.keyboard.press("/");
  const tag = await page.evaluate(() => document.activeElement?.tagName);
  console.log("active after '/':", tag, "header search inputs:", await page.locator("header input").count());
});
