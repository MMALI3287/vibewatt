import { test, expect } from "@playwright/test";

test("local context and activity stay separately labelled even without usage", async ({ page }) => {
  await page.route("**/api/activity", route => route.fulfill({ json: {
    days: { "2026-09-28": 2, "2026-09-29": 1 }, current_streak: 2, longest_streak: 2, truncated: true,
  } }));
  await page.route("**/api/context", route => route.fulfill({ json: [{
    session: "s1", source: "claude-code", used_tokens: 180000, max_tokens: 200000,
    severity: "urgent", snapshot_at: "2026-09-29T12:00:00Z",
  }] }));
  await page.goto("/?project=absent");
  await expect(page.getByRole("heading", { name: "Activity calendar · local history" })).toBeVisible();
  await expect(page.getByText("Current streak: 2 days · Longest streak: 2 days")).toBeVisible();
  await expect(page.getByText(/History input was truncated/)).toBeVisible();
  await expect(page.getByRole("link", { name: /180,000.*200,000/ })).toBeVisible();
});
