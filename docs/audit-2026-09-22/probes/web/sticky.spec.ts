import { test } from "@playwright/test";
import * as fs from "node:fs";

const OUT = "test-results/audit-d3b-sticky.json";

test("sticky filter bar vs focused element (reverse tabbing)", async ({ page }) => {
  test.setTimeout(300_000);
  const out: Record<string, unknown> = {};
  for (const [w, h] of [[390, 800], [1440, 900]]) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto("/sessions"); await page.waitForLoadState("networkidle"); await page.waitForTimeout(300);
    const bar = await page.evaluate(() => Math.round((document.querySelector("form.filters") as HTMLElement).getBoundingClientRect().height));
    // Focus the last session link, then walk backwards through links.
    await page.locator("table[aria-label='Session list'] a").last().focus();
    const hidden: string[] = []; let checked = 0;
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Shift+Tab");
      const r = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        if (!el || el.closest("form.filters") || el.closest("header")) return null;
        const fb = (document.querySelector("form.filters") as HTMLElement).getBoundingClientRect();
        const rect = el.getBoundingClientRect();
        return { txt: (el.textContent ?? "").slice(0, 25), top: Math.round(rect.top), bottom: Math.round(rect.bottom), barBottom: Math.round(fb.bottom) };
      });
      if (!r) continue;
      checked++;
      if (r.bottom <= r.barBottom) hidden.push(`${r.txt} top=${r.top} bottom=${r.bottom} barBottom=${r.barBottom}`);
    }
    out[`${w}`] = { filterBarHeight: bar, viewportH: h, checked, fullyObscured: hidden.length, sample: hidden.slice(0, 3) };
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
});
