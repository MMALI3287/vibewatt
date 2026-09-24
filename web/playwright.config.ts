import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8778;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${API_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Exercise the production server, avoiding Vite preview proxy and cold-start differences.
  webServer: {
    command: `uv run --project .. python e2e/fixture_server.py ${API_PORT}`,
    url: `http://127.0.0.1:${API_PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
