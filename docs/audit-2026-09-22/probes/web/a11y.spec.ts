import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs";

const OUT = "test-results/audit-d3b-a11y.json";
const report: Record<string, unknown> = {};
const save = () => { let prev: Record<string, unknown> = {}; try { prev = JSON.parse(fs.readFileSync(OUT, "utf-8")); } catch { /* first write */ } fs.writeFileSync(OUT, JSON.stringify({ ...prev, ...report }, null, 1)); };

const LIGHT_BG = "rgb(252, 252, 251)";
const DARK_BG = "rgb(26, 26, 25)";

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(200);
}
const bodyBg = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const state = (page: Page) => page.evaluate(() => ({
  dataTheme: document.documentElement.dataset.theme ?? null,
  stored: (() => { try { return localStorage.getItem("ccburn-theme"); } catch { return "ERR"; } })(),
  scheme: getComputedStyle(document.documentElement).colorScheme,
  bg: getComputedStyle(document.body).backgroundColor,
  button: Array.from(document.querySelectorAll("button")).find(b => b.textContent?.startsWith("Theme"))?.textContent ?? null,
}));

test.afterEach(() => save());

test("theme toggle persists and wins over OS both ways", async ({ page }) => {
  const log: unknown[] = [];
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/"); await settle(page);
  log.push({ step: "os-dark system", ...(await state(page)) });
  expect(await bodyBg(page)).toBe(DARK_BG);
  await page.getByRole("button", { name: /^Theme:/ }).click();
  log.push({ step: "os-dark after 1 click", ...(await state(page)) });
  await page.reload(); await settle(page);
  const a = await state(page); log.push({ step: "os-dark light reload", ...a });
  expect(a.dataTheme).toBe("light"); expect(a.bg).toBe(LIGHT_BG); expect(a.scheme).toBe("light");

  await page.emulateMedia({ colorScheme: "light" });
  await page.getByRole("button", { name: /^Theme:/ }).click();
  await page.reload(); await settle(page);
  const b = await state(page); log.push({ step: "os-light dark reload", ...b });
  expect(b.dataTheme).toBe("dark"); expect(b.bg).toBe(DARK_BG); expect(b.scheme).toBe("dark");

  await page.getByRole("button", { name: /^Theme:/ }).click();
  await page.reload(); await settle(page);
  const c = await state(page); log.push({ step: "os-light system reload", ...c });
  expect(c.dataTheme).toBeNull(); expect(c.stored).toBeNull(); expect(c.bg).toBe(LIGHT_BG);
  await page.emulateMedia({ colorScheme: "dark" });
  log.push({ step: "system follows OS change live", ...(await state(page)) });
  expect(await bodyBg(page)).toBe(DARK_BG);
  report.theme = log;
});

test("keyboard order, focus visibility, tab-stop count on overview", async ({ page }) => {
  await page.goto("/"); await settle(page);
  const seq: unknown[] = [];
  let heat = 0; let total = 0; const noOutline: string[] = [];
  for (let i = 0; i < 1500; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      return {
        tag: el.tagName, cls: String(el.className), name: (el.getAttribute("aria-label") || el.textContent || (el as HTMLInputElement).name || "").trim().slice(0, 40),
        outline: `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}`, fv: el.matches(":focus-visible"),
        path: el.closest("header") ? "header" : el.closest("form.filters") ? "filters" : el.closest("main") ? "main" : el.closest("footer") ? "footer" : "other",
      };
    });
    if (!info) { if (total > 5) break; else continue; }
    total++;
    if (info.cls.includes("heat-cell")) heat++;
    if (seq.length < 25) seq.push(info);
    if (!info.outline.startsWith("solid") && noOutline.length < 10) noOutline.push(`${info.tag}.${info.cls} ${info.name} ${info.outline}`);
  }
  const days = await page.locator(".heat-cell").count();
  report.keyboard = { firstStops: seq, totalTabStops: total, heatCellTabStops: heat, heatCells: days, noVisibleOutline: noOutline };
  const skip = await page.evaluate(() => Array.from(document.querySelectorAll("a")).some(a => /skip/i.test(a.textContent ?? "")));
  (report.keyboard as Record<string, unknown>).skipLink = skip;
  // Slash-to-focus search (PLAN 6 Navigation)
  await page.goto("/sessions"); await settle(page);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("/");
  (report.keyboard as Record<string, unknown>).slashFocusesSearch = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.getAttribute("type") === "search");
});

test("session modal: open by keyboard, focus contained, Escape restores focus", async ({ page }) => {
  await page.goto("/sessions"); await settle(page);
  const link = page.locator("table[aria-label='Session list'] a").first();
  const href = await link.getAttribute("href");
  await link.focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector("dialog[open]"); await settle(page);
  const opened = await page.evaluate(() => ({ active: document.activeElement?.textContent, inDialog: !!document.activeElement?.closest("dialog"), url: location.pathname }));
  const escapes: string[] = [];
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const where = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return "body";
      return el.closest("dialog") ? "dialog" : `${el.tagName}.${(el as HTMLElement).className}:${(el.textContent ?? "").slice(0, 20)}`;
    });
    if (where !== "dialog" && where !== "body") escapes.push(where);
  }
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
  const closed = await page.evaluate(() => ({
    open: !!document.querySelector("dialog[open]"), url: location.pathname,
    activeHref: (document.activeElement as HTMLAnchorElement | null)?.getAttribute("href") ?? document.activeElement?.tagName,
  }));
  report.modal = { href, opened, focusEscapedDialog: escapes, closed };
  expect(opened.inDialog).toBe(true);
  expect(escapes).toEqual([]);
  expect(closed.open).toBe(false);
  expect(closed.activeHref).toBe(href);
});

test("tables sortable by keyboard; names on buttons, inputs, charts", async ({ page }) => {
  await page.goto("/projects"); await settle(page);
  const th = page.locator("th").filter({ has: page.locator("button", { hasText: /^Tokens/ }) }).first();
  await th.locator("button").focus(); await page.keyboard.press("Enter");
  const sort1 = await th.getAttribute("aria-sort");
  await page.keyboard.press("Enter");
  const sort2 = await th.getAttribute("aria-sort");
  report.tableSort = { sort1, sort2 };
  const names: Record<string, unknown> = {};
  for (const route of ["/", "/sessions", "/projects", "/models", "/analysis", "/wrapped?year=2026", "/sessions/s0000"]) {
    await page.goto(route); await settle(page);
    await page.evaluate(() => document.querySelectorAll("details").forEach(d => { (d as HTMLDetailsElement).open = true; }));
    names[route] = await page.evaluate(() => {
      const nameOf = (el: Element) => (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.textContent || "").trim();
      const unnamedButtons = Array.from(document.querySelectorAll("button")).filter(b => !nameOf(b)).map(b => b.outerHTML.slice(0, 80));
      const unlabeled = Array.from(document.querySelectorAll("input, select, textarea")).filter(el => !el.getAttribute("aria-label") && !el.closest("label") && !(el.id && document.querySelector(`label[for="${el.id}"]`))).map(el => el.outerHTML.slice(0, 80));
      const svgs = Array.from(document.querySelectorAll("main svg")).filter(s => s.getBoundingClientRect().width > 50).map(s => ({
        cls: s.getAttribute("class"), role: s.getAttribute("role"), label: s.getAttribute("aria-label"), tabindex: s.getAttribute("tabindex"),
        titleEl: !!s.querySelector(":scope > title"), sectionLabel: s.closest("section")?.getAttribute("aria-label") ?? null,
      }));
      const heatNamedGeneric = document.querySelectorAll(".heat-cell[aria-label]:not([role])").length;
      const tables = Array.from(document.querySelectorAll("table")).map(t => t.getAttribute("aria-label") ?? t.querySelector("caption")?.textContent ?? t.closest("section")?.querySelector("h2")?.textContent ?? null);
      return { unnamedButtons, unlabeled, svgs, heatNamedGeneric, tables };
    });
  }
  report.names = names;
});

test("recharts tooltip colours, cursor, motion; daily bar tooltip", async ({ page }) => {
  const out: Record<string, unknown> = {};
  for (const [scheme, motion] of [["light", "reduce"], ["dark", "reduce"], ["light", "no-preference"]] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: motion });
    await page.goto("/"); await settle(page);
    const chart = page.locator(".hour-chart .recharts-wrapper").first();
    await chart.scrollIntoViewIfNeeded();
    const box = await chart.boundingBox();
    if (box) { await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5); await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5); }
    await page.waitForTimeout(300);
    out[`${scheme}-${motion}`] = await page.evaluate(() => {
      const item = document.querySelector(".recharts-tooltip-item");
      const label = document.querySelector(".recharts-tooltip-label");
      const cursor = document.querySelector(".recharts-tooltip-cursor");
      const wrap = document.querySelector(".recharts-tooltip-wrapper") as HTMLElement | null;
      const moving = Array.from(document.querySelectorAll("*")).map(el => { const cs = getComputedStyle(el); return { el, t: cs.transitionDuration, a: cs.animationName, tp: cs.transitionProperty }; })
        .filter(x => (x.t && x.t.split(",").some(d => parseFloat(d) > 0)) || (x.a && x.a !== "none"))
        .map(x => `${x.el.tagName}.${String((x.el as HTMLElement).className?.baseVal ?? (x.el as HTMLElement).className)} t=${x.t} p=${x.tp} a=${x.a}`).slice(0, 8);
      return {
        itemColor: item ? getComputedStyle(item).color : null, itemInline: item?.getAttribute("style") ?? null,
        labelColor: label ? getComputedStyle(label).color : null,
        cursorFill: cursor?.getAttribute("fill") ?? null, cursorComputed: cursor ? getComputedStyle(cursor).fill : null,
        wrapperTransition: wrap ? wrap.style.transition || getComputedStyle(wrap).transition : null,
        tpColor: getComputedStyle(document.body).color, reducedMotionCss: Array.from(document.styleSheets).some(s => { try { return Array.from(s.cssRules).some(r => r.cssText.includes("prefers-reduced-motion")); } catch { return false; } }),
        moving,
        dailyBarTitles: document.querySelectorAll(".chart svg rect.bar title").length,
      };
    });
  }
  report.recharts = out;
});

test("wrapped share card: aggregate-only SVG, renders under both schemes", async ({ page }) => {
  await page.goto("/wrapped?year=2026"); await settle(page);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download share card" }).click()]);
  const file = await download.path();
  const svg = fs.readFileSync(file!, "utf-8");
  const forbidden = ["alpha", "beta-service", "gamma", "extremely-long-project-name", "delta", "epsilon", "zeta", "theta", "iota", "Session ", "Cloud session", "Refactor-the-entire", "example/"];
  const leaked = forbidden.filter(f => svg.includes(f)).concat(/\beta\b/.test(svg) ? ["eta"] : []);
  const pageBiggest = await page.evaluate(() => Array.from(document.querySelectorAll("p")).find(p => p.textContent?.startsWith("Biggest session"))?.textContent ?? null);
  const render: Record<string, unknown> = {};
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setContent(`<img id="i" src="data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}">`);
    await page.waitForFunction(() => (document.getElementById("i") as HTMLImageElement).complete);
    render[scheme] = await page.evaluate(() => { const i = document.getElementById("i") as HTMLImageElement; return { w: i.naturalWidth, h: i.naturalHeight }; });
  }
  const texts = Array.from(svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)).map(m => m[1]);
  report.shareCard = { name: download.suggestedFilename(), bytes: svg.length, texts, fills: Array.from(svg.matchAll(/fill="([^"]+)"/g)).map(m => m[1]), leaked, render, pageBiggest };
  expect(leaked).toEqual([]);
});
