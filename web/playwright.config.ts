import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8778;
const WEB_PORT = 4178;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run --project .. python e2e/fixture_server.py ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite preview --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}/`,
      env: { CCBURN_API: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
