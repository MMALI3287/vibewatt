import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

const SHOTS = "test-results/audit-shots";
const OUT = "test-results/audit-d3b-overflow.json";
const LONG_PROJECT = "extremely-long-project-name-that-keeps-going-without-any-break-point-0123456789abcdefghijklmnop";

const ROUTES: [string, string][] = [
  ["overview", "/"],
  ["sessions", "/sessions"],
  ["projects", "/projects"],
  ["project-drill", `/projects?project=${LONG_PROJECT}`],
  ["models", "/models"],
  ["analysis", "/analysis"],
  ["wrapped", "/wrapped"],
  ["session-modal", "/sessions/s0000"],
  ["cloud-modal", "/sessions/cloud-00"],
];
const WIDTHS = [1440, 1024, 768, 390];
const THEMES = ["light", "dark"] as const;

async function measure(page: Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const cw = de.clientWidth;
    const offenders: string[] = [];
    const scrollers: string[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const r = el.getBoundingClientRect();
      if (!r.width && !r.height) continue;
      const cs = getComputedStyle(el);
      if ((cs.overflowX === "auto" || cs.overflowX === "scroll") && el.scrollWidth > el.clientWidth + 1) {
        const cls = (el as HTMLElement).className;
        if (!/table-wrap|heatmap-wrap/.test(String(cls))) scrollers.push(`${el.tagName}.${cls} sw=${el.scrollWidth} cw=${el.clientWidth}`);
      }
      if (r.right > cw + 1 || r.left < -1) {
        let p = el.parentElement;
        let clipped = false;
        while (p && p !== document.body) {
          const s = getComputedStyle(p);
          if (["auto", "scroll", "hidden", "clip"].includes(s.overflowX) || p.tagName === "DIALOG") { clipped = true; break; }
          p = p.parentElement;
        }
        if (!clipped && el.closest("dialog") === null) offenders.push(`${el.tagName}.${(el as HTMLElement).className} right=${Math.round(r.right)}`);
      }
    }
    const dlg = document.querySelector("dialog[open]") as HTMLElement | null;
    return {
      overflow: de.scrollWidth - cw, cw, offenders: offenders.slice(0, 6), scrollers: scrollers.slice(0, 6),
      dialog: dlg ? { sw: dlg.scrollWidth, cw: dlg.clientWidth } : null,
      bg: getComputedStyle(document.body).backgroundColor, theme: de.dataset.theme ?? "system",
    };
  });
}

test("routes x widths x themes: zero horizontal page overflow", async ({ page }) => {
  test.setTimeout(900_000);
  const results: Record<string, unknown>[] = [];
  const failures: string[] = [];
  for (const theme of THEMES) {
    await page.emulateMedia({ colorScheme: theme });
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      for (const [name, route] of ROUTES) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(250);
        const collapsed = await measure(page);
        // Expand every <details> so hidden tables are measured too.
        await page.evaluate(() => document.querySelectorAll("details").forEach(d => { (d as HTMLDetailsElement).open = true; }));
        await page.waitForTimeout(250);
        const expanded = await measure(page);
        const row = { theme, width, name, collapsed, expanded };
        results.push(row);
        for (const [state, m] of [["collapsed", collapsed], ["expanded", expanded]] as const) {
          if (m.overflow > 0) failures.push(`${theme} ${width} ${name} ${state}: page overflow ${m.overflow}px ${m.offenders.join(" | ")}`);
          if (m.scrollers.length) failures.push(`${theme} ${width} ${name} ${state}: non-table scroller ${m.scrollers.join(" | ")}`);
          if (m.dialog && m.dialog.sw > m.dialog.cw + 1) failures.push(`${theme} ${width} ${name} ${state}: dialog h-scroll ${m.dialog.sw}/${m.dialog.cw}`);
        }
        const shoot = (theme === "light" && width === 1440) || (theme === "dark" && width === 390);
        if (shoot) {
          await page.goto(route);
          await page.waitForLoadState("networkidle");
          await page.waitForTimeout(300);
          await page.screenshot({ path: path.join(SHOTS, `${name}-${width}-${theme}.png`), fullPage: true });
        }
      }
    }
  }
  fs.writeFileSync(OUT, JSON.stringify({ failures, results }, null, 1));
  console.log(`checked ${results.length} route/width/theme combos`);
  console.log(failures.join("\n") || "NO OVERFLOW FAILURES");
  expect(failures).toEqual([]);
});
