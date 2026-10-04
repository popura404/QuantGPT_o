import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  outputDir: "../test-results/playwright",
  reporter: [["list"], ["json", { outputFile: "../test-results/playwright-results.json" }]],
  use: { baseURL: "http://127.0.0.1:8017", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "edge", use: { ...devices["Desktop Edge"], channel: "msedge" } }],
  webServer: {
    command: process.platform === "win32" ? "..\\.venv312\\Scripts\\python.exe e2e/server.py" : "../.venv/bin/python e2e/server.py",
    url: "http://127.0.0.1:8017/api/v1/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
