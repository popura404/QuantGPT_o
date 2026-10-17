import { defineConfig, devices } from "@playwright/test";

const python = process.env.PLAYWRIGHT_PYTHON ?? (process.platform === "win32"
  ? "..\\.venv312\\Scripts\\python.exe"
  : "../.venv/bin/python");
const channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: false,
  workers: 1,
  outputDir: "../test-results/playwright",
  reporter: [["list"], ["json", { outputFile: "../test-results/playwright-results.json" }]],
  use: { baseURL: "http://127.0.0.1:8017", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: channel ?? "chromium", use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) } }],
  webServer: {
    command: `"${python}" e2e/server.py`,
    url: "http://127.0.0.1:8017/api/v1/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
