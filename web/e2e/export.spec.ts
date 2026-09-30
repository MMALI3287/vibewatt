import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

interface Capture {
  html: string;
  width: number;
  height: number;
  clipped: string[];
  background: string;
}

for (const theme of ["light", "dark"]) {
  for (const width of [1440, 390]) {
    test(`PNG includes every view without clipping at ${width}px in ${theme}`, async ({ page }, testInfo) => {
      test.setTimeout(120_000);
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(theme => {
        localStorage.setItem("vibewatt-theme", theme);
        const snapshots: Capture[] = [];
        Object.assign(window, { exportCaptures: snapshots });
        new MutationObserver(() => {
          const capture = document.querySelector<HTMLElement>(".dashboard-export");
          if (!capture || snapshots.length && snapshots.at(-1)?.html === capture.outerHTML) return;
          const box = capture.getBoundingClientRect();
          const clipped = [...capture.querySelectorAll("table, .heatmap, svg, footer")].filter(node => {
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && (rect.left < box.left - 1 || rect.right > box.right + 1 || rect.bottom > box.bottom + 1);
          }).map(node => `${node.tagName}: ${JSON.stringify(node.getBoundingClientRect().toJSON())} outside ${JSON.stringify(box.toJSON())}`);
          snapshots.push({ html: capture.outerHTML, width: Math.ceil(box.width), height: Math.ceil(box.height),
            clipped, background: getComputedStyle(capture).backgroundColor });
        }).observe(document, { childList: true, subtree: true });
      }, theme);
      for (const route of ["/", "/sessions", "/projects?project=demo", "/models", "/analysis", "/wrapped"]) {
        await page.goto(route);
        await expect(page.locator("#main h1").first()).toBeVisible();
        await expect(page.locator("#main")).not.toContainText(/Loading (view|sessions|projects|models|findings|Wrapped)/);
        await expect(page.locator(".footer")).toContainText("Local logs");
        const downloadPromise = page.waitForEvent("download");
        await page.getByRole("button", { name: "Export PNG", exact: true }).click();
        const download = await downloadPromise;
        const path = await download.path();
        expect(path).not.toBeNull();
        const png = await readFile(path!);
        await download.saveAs(testInfo.outputPath(`vibewatt-${route.split("?")[0].replaceAll("/", "") || "overview"}.png`));
        expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
        const captures = await page.evaluate(() => (window as typeof window & { exportCaptures: Capture[] }).exportCaptures);
        const capture = captures.at(-1)!;
        expect(capture).toBeDefined();
        expect(capture.html).not.toContain("demo");
        expect(capture.html).not.toContain("cloud-project");
        expect(capture.html).toContain("Local logs");
        expect(capture.html).toContain("cloud-reported");
        expect(capture.clipped).toEqual([]);
        expect(png.readUInt32BE(16)).toBe(capture.width);
        expect(png.readUInt32BE(20)).toBe(capture.height);
        expect(capture.background).toBe(theme === "light" ? "rgb(252, 252, 251)" : "rgb(26, 26, 25)");
        expect(png.length).toBeGreaterThan(1000);
        if (route.includes("project=demo")) {
          expect(capture.html).toContain("Project 2");
          expect(capture.html).toContain("fix the sync");
          expect(capture.html).toContain("Session list");
        }
      }
    });
  }
}

test("PNG requires complete masking facets unless project names are explicitly included", async ({ page }) => {
  await page.route("**/api/session-facets", route => route.fulfill({ status: 503, body: "unavailable" }));
  await page.goto("/projects?project=demo");
  await expect(page.getByRole("table", { name: "Projects", exact: true })).toContainText("demo");
  await page.getByRole("button", { name: "Export PNG", exact: true }).click();
  await expect(page.locator(".dashboard-export-controls [role=alert]")).toContainText("Project names could not be loaded");
  await expect(page.locator(".dashboard-export")).toHaveCount(0);
  await page.getByRole("checkbox", { name: "Include project names" }).check();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export PNG", exact: true }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("vibewatt-projects.png");
});
