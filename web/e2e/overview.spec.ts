import { expect, test, type Page } from "@playwright/test";

const WIDTHS = [1440, 1024, 768, 390];

async function pageOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

for (const width of WIDTHS) {
  test(`no horizontal page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("kpi-responses")).toBeVisible();
    // Open the wide table too: it must scroll inside its own container, not push the page.
    await page.locator("details summary").first().click();
    expect(await pageOverflow(page)).toBe(0);
  });
}

test("reloading a filtered URL restores the same view", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("kpi-responses")).toBeVisible();
  const unfiltered = await page.getByTestId("kpi-responses").textContent();

  const source = page.locator('select[name="source"]');
  const options = await source.locator("option").evaluateAll((els) =>
    // "all" and "claude" are scopes, not single sources; the fixture has only Claude data.
    els.map((e) => (e as HTMLOptionElement).value).filter((v) => v !== "all" && v !== "claude"),
  );
  expect(options.length).toBeGreaterThan(1);
  await source.selectOption(options[0]);
  await page.locator('select[name="metric"]').selectOption("tokens");

  await expect(page).toHaveURL(new RegExp(`source=${encodeURIComponent(options[0])}`));
  await expect(page).toHaveURL(/metric=tokens/);
  const filtered = page.getByTestId("kpi-responses");
  await expect(filtered).not.toHaveText(unfiltered ?? "");
  const kpi = await filtered.textContent();
  const hero = await page.getByTestId("hero-number").textContent();
  const url = page.url();

  await page.reload();

  expect(page.url()).toBe(url);
  await expect(source).toHaveValue(options[0]);
  await expect(page.locator('select[name="metric"]')).toHaveValue("tokens");
  await expect(page.getByTestId("kpi-responses")).toHaveText(kpi ?? "");
  await expect(page.getByTestId("hero-number")).toHaveText(hero ?? "");
});

test("tool usage distinguishes unavailable execution cost", async ({ page }) => {
  await page.route("**/api/summary**", async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.total.web_fetch = 3;
    data.total.code_execution = 2;
    data.total.code_execution_cost = "unavailable";
    await route.fulfill({ response, json: data });
  });
  await page.goto("/");
  await expect(page.getByText(/Web fetches: 3/)).toBeVisible();
  await expect(page.getByText(/Code execution cost: unavailable/)).toBeVisible();
});
