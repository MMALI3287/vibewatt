import { test } from "@playwright/test";
import * as fs from "node:fs";

const OUT = "test-results/audit-d3b-extra2.json";

test("grid children min-width, headings, titles, gutters", async ({ page }) => {
  test.setTimeout(300_000);
  const out: Record<string, unknown> = {};
  await page.setViewportSize({ width: 390, height: 800 });
  for (const route of ["/", "/sessions", "/projects", "/models", "/analysis", "/wrapped?year=2026", "/sessions/s0000"]) {
    await page.goto(route); await page.waitForLoadState("networkidle"); await page.waitForTimeout(250);
    await page.evaluate(() => document.querySelectorAll("details").forEach(d => { (d as HTMLDetailsElement).open = true; }));
    out[route] = await page.evaluate(() => {
      const autoMin: string[] = [];
      for (const el of Array.from(document.querySelectorAll("body *"))) {
        const p = el.parentElement; if (!p) continue;
        const pd = getComputedStyle(p).display;
        if (pd !== "grid" && pd !== "inline-grid") continue;
        if (p.classList.contains("heatmap")) continue;
        const cs = getComputedStyle(el);
        if (cs.display === "none" || cs.position === "absolute") continue;
        if (cs.minWidth === "auto") autoMin.push(`${p.tagName}.${p.className} > ${el.tagName}.${String((el as HTMLElement).className)}`);
      }
      const main = document.querySelector("main") as HTMLElement;
      const dlg = document.querySelector("dialog[open]") as HTMLElement | null;
      return {
        gridChildrenMinWidthAuto: [...new Set(autoMin)].slice(0, 15),
        h1: Array.from(document.querySelectorAll("h1")).map(h => h.textContent),
        title: document.title,
        mainPad: [getComputedStyle(main).paddingLeft, getComputedStyle(main).paddingRight],
        dialogLeft: dlg ? Math.round(dlg.getBoundingClientRect().left) : null,
      };
    });
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
});
