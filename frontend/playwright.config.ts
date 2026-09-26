import { defineConfig } from "@playwright/test";

const deploymentURL = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: "list",
  use: {
    baseURL: deploymentURL || "http://127.0.0.1:5175",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: deploymentURL
    ? undefined
    : {
        command: "npm run dev -- --port 5175 --strictPort",
        url: "http://127.0.0.1:5175",
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
      },
});
