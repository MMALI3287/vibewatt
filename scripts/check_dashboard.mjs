import { chromium } from "../web/node_modules/@playwright/test/index.mjs";

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const base = process.argv[2];
  for (const theme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: theme });
    for (const route of ["/", "/sessions/s1", "/projects?project=demo", "/models", "/analysis", "/wrapped?year=2026"]) {
      await page.goto(base + route);
      await page.waitForLoadState("networkidle");
      await page.getByRole("heading", { level: 1 }).waitFor();
      if (route === "/sessions/s1") await page.getByRole("dialog").waitFor();
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Overflow: ${route}`);
      if (await page.getByRole("alert").count()) throw new Error(`API failure: ${route}`);
    }
  }
  if (errors.length) throw new Error(errors.join("\n"));
  console.log("PASS installed dashboard: six routes, both themes, session deep link, no runtime errors");
} finally {
  await browser.close();
}
