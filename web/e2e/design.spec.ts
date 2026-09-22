import { expect, test, type Page } from "@playwright/test";

// Phase 6.5g design gates (A-038, A-040, A-062, A-084, A-085, A-112).
const ROUTES = [
  "/", "/sessions", "/projects", "/projects?project=demo", "/models",
  "/analysis?source=claude-code&project=demo", "/wrapped?year=2026", "/sessions/s1",
];
const THEMES = ["light", "dark"] as const;

async function settle(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  if (path.startsWith("/sessions/")) await expect(page.getByRole("dialog")).toBeVisible();
}

for (const theme of THEMES) {
  for (const width of [1440, 1024, 768, 390]) {
    test(`no page overflow at ${width}px in the ${theme} theme`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      for (const path of ROUTES) {
        await settle(page, path);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${path} overflows by ${overflow}px`).toBe(0);
      }
    });
  }

  test(`text meets WCAG AA contrast in the ${theme} theme`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const path of ROUTES) {
      await settle(page, path);
      // Open collapsed sections so their text is checked too.
      await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
      const failures = await page.evaluate(contrastFailures);
      expect(failures, `${path}: ${JSON.stringify(failures.slice(0, 5))}`).toEqual([]);
    }
  });
}

test("the theme toggle cycles and survives a reload", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: /^Theme:/ });
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await expect(toggle).toHaveText("Theme: system");
  await toggle.click();
  await expect(toggle).toHaveText("Theme: light");
  const light = await background();
  await toggle.click();
  await expect(toggle).toHaveText("Theme: dark");
  expect(await background()).not.toBe(light);
  await page.reload();
  await expect(page.getByRole("button", { name: /^Theme:/ })).toHaveText("Theme: dark");
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
});

test("skip link, one heatmap tab stop, arrow keys and per-route titles", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Overview · vibewatt");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await skip.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  const cells = page.getByRole("gridcell");
  expect(await cells.count()).toBeGreaterThan(1);
  expect(await page.locator('[role="gridcell"][tabindex="0"]').count()).toBe(1);
  await page.locator('[role="gridcell"][tabindex="0"]').focus();
  const before = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await page.keyboard.press("ArrowUp");
  const after = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  expect(after).not.toBe(before);
  for (const [path, title] of [["/sessions", "Sessions"], ["/analysis", "Analysis"], ["/wrapped", "Wrapped"], ["/models", "Models"]]) {
    await page.goto(path);
    await expect(page).toHaveTitle(`${title} · vibewatt`);
  }
});

test("the sticky filter bar never hides the focused element", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.goto("/sessions");
  await page.waitForLoadState("networkidle");
  const bar = await page.getByRole("form", { name: "Filters" }).boundingBox();
  const links = page.getByRole("table", { name: "Session list" }).getByRole("link");
  const last = links.last();
  await last.focus();
  await page.keyboard.press("Shift+Tab");
  const focused = await page.evaluate(() => {
    const r = document.activeElement!.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom };
  });
  expect(bar).not.toBeNull();
  expect(focused.bottom).toBeGreaterThan(bar!.y + bar!.height);
});

/** Runs in the page. An axe-style colour-contrast check for visible text. */
function contrastFailures(): { text: string; ratio: number; need: number }[] {
  const parse = (value: string): number[] | null => {
    const m = value.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  };
  const lum = ([r, g, b]: number[]) => {
    const c = [r, g, b].map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const blend = (top: number[], under: number[]) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1);
  const background = (el: Element): number[] => {
    const layers: number[][] = [];
    for (let node: Element | null = el; node; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg[3] > 0) {
        layers.push(bg);
        if (bg[3] >= 1) break;
      }
    }
    let colour = [255, 255, 255, 1];
    for (const layer of layers.reverse()) colour = blend(layer, colour);
    return colour;
  };
  const failures: { text: string; ratio: number; need: number }[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement;
    if (!el || seen.has(el) || !node.textContent?.trim()) continue;
    seen.add(el);
    if (el.closest("svg, [aria-hidden='true'], button:disabled, option, .recharts-wrapper")) continue;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (style.visibility === "hidden" || style.display === "none" || rect.width === 0 || rect.height === 0) continue;
    const fg = parse(style.color);
    if (!fg) continue;
    const bg = background(el);
    const [a, b] = [lum(blend(fg, bg)), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < need - 0.01) failures.push({ text: node.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100, need });
  }
  return failures;
}
