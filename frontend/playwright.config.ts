import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "../.local/playwright-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8765",
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "..\\.venv\\Scripts\\python.exe tests/serve_fixture.py",
    url: "http://127.0.0.1:8765/healthz",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
